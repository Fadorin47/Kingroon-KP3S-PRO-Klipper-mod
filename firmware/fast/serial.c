// STM32 serial
// DMA additions: Aregus393, GPLv3, fb5-native-display e1836310.
//
// Copyright (C) 2019  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include "autoconf.h" // CONFIG_SERIAL_BAUD
#include "board/armcm_boot.h" // armcm_enable_irq
#include "board/irq.h" // irq_save
#include "board/misc.h" // timer_read_time
#include "board/serial_irq.h" // serial_rx_byte
#include "command.h" // DECL_CONSTANT_STR
#include "internal.h" // enable_pclock
#include "sched.h" // DECL_INIT

// Select the configured serial port
#if CONFIG_STM32_SERIAL_USART1
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PA10,PA9");
  #define GPIO_Rx GPIO('A', 10)
  #define GPIO_Tx GPIO('A', 9)
  #define GPIO_AF_MODE 7
  #define USARTx USART1
  #define USARTx_IRQn USART1_IRQn
#elif CONFIG_STM32_SERIAL_USART1_ALT_PB7_PB6
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PB7,PB6");
  #define GPIO_Rx GPIO('B', 7)
  #define GPIO_Tx GPIO('B', 6)
  #define GPIO_AF_MODE 7
  #define USARTx USART1
  #define USARTx_IRQn USART1_IRQn
#elif CONFIG_STM32_SERIAL_USART2
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PA3,PA2");
  #define GPIO_Rx GPIO('A', 3)
  #define GPIO_Tx GPIO('A', 2)
  #define GPIO_AF_MODE 7
  #define USARTx USART2
  #define USARTx_IRQn USART2_IRQn
#elif CONFIG_STM32_SERIAL_USART2_ALT_PD6_PD5
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PD6,PD5");
  #define GPIO_Rx GPIO('D', 6)
  #define GPIO_Tx GPIO('D', 5)
  #define GPIO_AF_MODE 7
  #define USARTx USART2
  #define USARTx_IRQn USART2_IRQn
#elif CONFIG_STM32_SERIAL_USART3
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PB11,PB10");
  #define GPIO_Rx GPIO('B', 11)
  #define GPIO_Tx GPIO('B', 10)
  #define GPIO_AF_MODE 7
  #define USARTx USART3
  #define USARTx_IRQn USART3_IRQn
#elif CONFIG_STM32_SERIAL_USART3_ALT_PD9_PD8
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PD9,PD8");
  #define GPIO_Rx GPIO('D', 9)
  #define GPIO_Tx GPIO('D', 8)
  #define GPIO_AF_MODE 7
  #define USARTx USART3
  #define USARTx_IRQn USART3_IRQn
#elif CONFIG_STM32_SERIAL_USART6
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PA12,PA11");
  #define GPIO_Rx GPIO('A', 12)
  #define GPIO_Tx GPIO('A', 11)
  #define GPIO_AF_MODE 8
  #define USARTx USART6
  #define USARTx_IRQn USART6_IRQn
#elif CONFIG_STM32_SERIAL_USART6_ALT_PC7_PC6
  DECL_CONSTANT_STR("RESERVE_PINS_serial", "PC7,PC6");
  #define GPIO_Rx GPIO('C', 7)
  #define GPIO_Tx GPIO('C', 6)
  #define GPIO_AF_MODE 8
  #define USARTx USART6
  #define USARTx_IRQn USART6_IRQn
#endif

#if CONFIG_STM32_SERIAL_RX_DMA
/****************************************************************
 * Receive with DMA (stm32f1)
 ****************************************************************/

// The usart has no fifo, so at high baud rates received bytes are lost
// while timer dispatch runs with irqs disabled (many steppers moving).
// The dma controller stores every byte in a ring buffer regardless of
// the irq state, and a short periodic timer passes new bytes on to
// serial_rx_byte(). The IDLE flag is not used: clearing it on the
// stm32f1 needs a DR read, which could take a byte away from the dma.

#if CONFIG_STM32_SERIAL_USART1 || CONFIG_STM32_SERIAL_USART1_ALT_PB7_PB6
  #define RX_DMA_CHANNEL DMA1_Channel5
  #define RX_DMA_IFCR DMA_IFCR_CGIF5
  #define RX_DMA_IRQn DMA1_Channel5_IRQn
#elif CONFIG_STM32_SERIAL_USART2 || CONFIG_STM32_SERIAL_USART2_ALT_PD6_PD5
  #define RX_DMA_CHANNEL DMA1_Channel6
  #define RX_DMA_IFCR DMA_IFCR_CGIF6
  #define RX_DMA_IRQn DMA1_Channel6_IRQn
#elif CONFIG_STM32_SERIAL_USART3 || CONFIG_STM32_SERIAL_USART3_ALT_PD9_PD8
  #define RX_DMA_CHANNEL DMA1_Channel3
  #define RX_DMA_IFCR DMA_IFCR_CGIF3
  #define RX_DMA_IRQn DMA1_Channel3_IRQn
#else
  #error "No receive dma channel known for this serial port"
#endif

#define RX_RING_SIZE 256        // more than the 192 byte receive window
#define RX_POLL_US 250

static uint8_t rx_ring[RX_RING_SIZE];
static uint32_t rx_tail;
static struct timer rx_timer;

// Line error counters (the dma reads DR, which clears the SR flags after
// an SR read - so a flag only counts if new bytes arrived since the
// previous SR read)
static uint32_t rx_err_noise, rx_err_frame, rx_err_overrun, rx_err_head;

// Pass the bytes received since the last call on to the protocol code.
// Called from the timer and the dma irq (both with irqs disabled).
static void
rx_dma_drain(void)
{
    uint32_t sr = USARTx->SR;
    uint32_t head = (RX_RING_SIZE - RX_DMA_CHANNEL->CNDTR) % RX_RING_SIZE;
    if (head != rx_err_head) {
        if (sr & USART_SR_NE)
            rx_err_noise++;
        if (sr & USART_SR_FE)
            rx_err_frame++;
        if (sr & USART_SR_ORE)
            rx_err_overrun++;
        rx_err_head = head;
    }
    while (rx_tail != head) {
        serial_rx_byte(rx_ring[rx_tail]);
        rx_tail = (rx_tail + 1) % RX_RING_SIZE;
    }
}

// Half and full ring interrupts pick up bulk data without waiting for the
// poll timer (shorter round trip time, more throughput)
void
RX_DMA_IRQHandler(void)
{
    DMA1->IFCR = RX_DMA_IFCR;
    irqstatus_t flag = irq_save();
    rx_dma_drain();
    irq_restore(flag);
}

void
command_get_serial_errors(uint32_t *args)
{
    irqstatus_t flag = irq_save();
    uint32_t ne = rx_err_noise, fe = rx_err_frame, ore = rx_err_overrun;
    irq_restore(flag);
    sendf("serial_errors noise=%u frame=%u overrun=%u", ne, fe, ore);
}
DECL_COMMAND_FLAGS(command_get_serial_errors, HF_IN_SHUTDOWN,
                   "get_serial_errors");

static uint_fast8_t
rx_dma_event(struct timer *t)
{
    rx_dma_drain();
    t->waketime += timer_from_us(RX_POLL_US);
    return SF_RESCHEDULE;
}

static void
rx_dma_timer_start(void)
{
    rx_timer.func = rx_dma_event;
    rx_timer.waketime = timer_read_time() + timer_from_us(RX_POLL_US);
    sched_add_timer(&rx_timer);
}

// All timers are dropped on shutdown, but commands (eg, a firmware
// restart request) must still be received
void
rx_dma_shutdown(void)
{
    rx_dma_timer_start();
}
DECL_SHUTDOWN(rx_dma_shutdown);

static void
rx_dma_init(void)
{
    RCC->AHBENR |= RCC_AHBENR_DMA1EN;
    RX_DMA_CHANNEL->CCR = 0;
    DMA1->IFCR = RX_DMA_IFCR;
    RX_DMA_CHANNEL->CPAR = (uint32_t)&USARTx->DR;
    RX_DMA_CHANNEL->CMAR = (uint32_t)rx_ring;
    RX_DMA_CHANNEL->CNDTR = RX_RING_SIZE;
    RX_DMA_CHANNEL->CCR = (DMA_CCR_MINC | DMA_CCR_CIRC | DMA_CCR_PL_1
                           | DMA_CCR_HTIE | DMA_CCR_TCIE | DMA_CCR_EN);
    rx_tail = rx_err_head = 0;
    armcm_enable_irq(RX_DMA_IRQHandler, RX_DMA_IRQn, 1);
    USARTx->CR3 |= USART_CR3_DMAR;
    rx_dma_timer_start();
}

#define CR1_RX_FLAGS 0
#else
#define CR1_RX_FLAGS USART_CR1_RXNEIE
#endif

#define CR1_FLAGS (USART_CR1_UE | USART_CR1_RE | USART_CR1_TE   \
                   | CR1_RX_FLAGS)

void
USARTx_IRQHandler(void)
{
    uint32_t sr = USARTx->SR;
#if !CONFIG_STM32_SERIAL_RX_DMA
    if (sr & (USART_SR_RXNE | USART_SR_ORE)) {
        // The ORE flag is automatically cleared by reading SR, followed
        // by reading DR.
        serial_rx_byte(USARTx->DR);
    }
#endif
    if (sr & USART_SR_TXE && USARTx->CR1 & USART_CR1_TXEIE) {
        uint8_t data;
        int ret = serial_get_tx_byte(&data);
        if (ret)
            USARTx->CR1 = CR1_FLAGS;
        else
            USARTx->DR = data;
    }
}

void
serial_enable_tx_irq(void)
{
    USARTx->CR1 = CR1_FLAGS | USART_CR1_TXEIE;
}

void
serial_init(void)
{
    enable_pclock((uint32_t)USARTx);

    uint32_t pclk = get_pclock_frequency((uint32_t)USARTx);
    uint32_t div = DIV_ROUND_CLOSEST(pclk, CONFIG_SERIAL_BAUD);
    USARTx->BRR = (((div / 16) << USART_BRR_DIV_Mantissa_Pos)
                   | ((div % 16) << USART_BRR_DIV_Fraction_Pos));
#if CONFIG_STM32_SERIAL_RX_DMA
    // Set up the dma before the receiver is enabled
    rx_dma_init();
#endif
    USARTx->CR1 = CR1_FLAGS;
    armcm_enable_irq(USARTx_IRQHandler, USARTx_IRQn, 0);

    gpio_peripheral(GPIO_Rx, GPIO_FUNCTION(GPIO_AF_MODE), 1);
    gpio_peripheral(GPIO_Tx, GPIO_FUNCTION(GPIO_AF_MODE), 0);
}
DECL_INIT(serial_init);
