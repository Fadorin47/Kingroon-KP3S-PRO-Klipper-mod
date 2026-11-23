# Источники и авторство

## Код и техническая документация

| Источник | Что использовано |
| --- | --- |
| [Klipper, ревизия 7a723bdc](https://github.com/Klipper3d/klipper/commit/7a723bdc1cb5e292ca0cd7b708035a7249b5e9a7) | Базовый код хоста и MCU. Расширения панели используют интерфейсы Klipper. Уведомление Kevin O’Connor сохранено в исходнике USART. |
| [Aregus393 — FB5 native display, d45a781](https://github.com/Aregus393/FB5-klipperscreen-for-native-display/tree/d45a7810fa7f3980b786a8aa1c774c09affb303e) | Основа модулей Klippy для штатного дисплея и XPT2046. |
| [Aregus393 — форк Klipper, e183631](https://github.com/Aregus393/klipper/commit/e1836310fbebd004434cb40d6e2ef4353a0df080) | Основа MCU-драйвера FSMC и приёма USART3 через DMA. |
| [Marlin, 1d89f1c](https://github.com/MarlinFirmware/Marlin/tree/1d89f1c9155537bb63d213fc285d2c9c98378361) | Регистр и последовательности инициализации [ST7789V](https://github.com/MarlinFirmware/Marlin/blob/1d89f1c9155537bb63d213fc285d2c9c98378361/Marlin/src/lcd/tft_io/st7789v.h) и [ILI9341](https://github.com/MarlinFirmware/Marlin/blob/1d89f1c9155537bb63d213fc285d2c9c98378361/Marlin/src/lcd/tft_io/ili9341.h), [распиновка платы](https://github.com/MarlinFirmware/Marlin/blob/1d89f1c9155537bb63d213fc285d2c9c98378361/Marlin/src/pins/stm32f1/pins_MKS_ROBIN_NANO_common.h) и реализация [FSMC](https://github.com/MarlinFirmware/Marlin/blob/1d89f1c9155537bb63d213fc285d2c9c98378361/Marlin/src/HAL/STM32F1/tft/tft_fsmc.cpp). |
| [Marlin Configurations — Kingroon KP3S Pro](https://github.com/MarlinFirmware/Configurations/tree/import-2.1.x/config/examples/Kingroon/KP3S_Pro) | Конфигурация для сверки вариантов штатного TFT24 и платы. |
| [Klipper API Server](https://www.klipper3d.org/API_Server.html), [MCU Protocol](https://www.klipper3d.org/Protocol.html), [Status Reference](https://www.klipper3d.org/Status_Reference.html) | Формат сообщений локального сокета, транспорт и объекты состояния принтера. |
| [Klipper — Bed Mesh](https://www.klipper3d.org/Bed_Mesh.html#adaptive-meshes), [Bed Level](https://www.klipper3d.org/Manual_Level.html#adjusting-bed-leveling-screws-using-the-bed-probe), [Config Checks](https://www.klipper3d.org/Config_checks.html), [Rotation Distance](https://www.klipper3d.org/Rotation_Distance.html) | Калибровки, адаптивная карта, проверка конфигурации и настройка подачи. |
| [OrcaSlicer — Advanced Printer Settings](https://github.com/OrcaSlicer/OrcaSlicer/wiki/printer_basic_information_advanced) | Миниатюры и передаваемый слайсером прогресс `M73`. |

Оригинальные уведомления сохранены в файлах драйвера. Полный текст GPL v3 находится в [LICENSE](LICENSE).

## Интерфейс и макросы

- Для сравнения экранов использованы разделы управления и печати. [KlipperScreen](https://klipperscreen.github.io/KlipperScreen/Panels/) · [Prusa MK4 LCD Menu](https://help.prusa3d.com/article/lcd-menu-mk4-s-mk3-9-s_413171) · [Creality K1C, руководство, §3.1](https://wiki.creality.com/products/fully-enclosed/k1c-2025/%E5%A4%9A%E8%AF%AD%E8%A8%80%E8%AF%B4%E6%98%8E%E4%B9%A6/k1c-sm-001_user_manual%28en%29.pdf)
- Для сравнения макросов и калибровок использованы примеры сообщества. [Fluidd](https://docs.fluidd.xyz/configuration/initial_setup) · [jschuh/klipper-macros](https://github.com/jschuh/klipper-macros) · [KAMP](https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging) · [Prusa — Cold pull](https://help.prusa3d.com/article/cold-pull-mk3-s-mk2-5-s_2075)

## Инструменты и зависимости

- [Python](https://www.python.org/) и [Pillow](https://python-pillow.org/) для панели и миниатюр.
- [NumPy](https://numpy.org/) для необязательного ускорения кодирования кадра.
- [Jinja2](https://jinja.palletsprojects.com/) для тестов макросов.
- [DejaVu Fonts](https://dejavu-fonts.github.io/) для отрисовки текста на Linux, системный Arial для предварительного просмотра на Windows.
- [GNU Make](https://www.gnu.org/software/make/) и [Arm GNU Toolchain](https://developer.arm.com/Tools%20and%20Software/Arm%20GNU%20Toolchain) для сборки прошивки.
- [BIGTREETECH BTT Pi](https://github.com/bigtreetech/BTT-Pi) и [CB1](https://github.com/bigtreetech/CB1) — документация Linux-хоста.

## Печатные детали

Печатающая голова основана на **K3D Ender-3 Sprite Printhead** Дмитрия Соркина. Автор этой сборки адаптировал крепление к рельсовой каретке Kingroon. Подробности и сведения о других деталях приведены в [описании механической части](docs/hardware.md). [Документация и файлы K3D ESP](https://www.k3d.tech/projects/esp/) · [Архив релизов K3D ESP](https://www.k3d.tech/projects/esp/releases/)
