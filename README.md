# ipa-restore

**Восстановление сгруженных приложений iPhone по ссылке или ID App Store.**

[Русский](README.md) · [English](README.en.md)

Небольшая утилита на Python: получает IPA через ваш Apple Account, устанавливает приложение на iPhone по USB и проверяет установленную версию. Работает без Bmrng, jailbreak и установки Python-пакетов.

```sh
python3 ipa_restore.py setup
python3 ipa_restore.py login
python3 ipa_restore.py restore 771700826
```

Сценарий проверен на реальном iPhone: интерактивный вход в Apple Account, повторное скачивание «Почты России» 7.8.6, установка по USB и проверка версии.

## Быстрый старт

Нужны **macOS**, Python **3.10+**, iPhone с подключением по USB и Apple Account, на который приложение уже получено. Разблокируйте телефон и подтвердите «Доверять этому компьютеру», если iPhone спросит.

```sh
git clone https://github.com/KobetsDev/ipa-restore.git
cd ipa-restore
python3 ipa_restore.py setup
python3 ipa_restore.py login

# Найти ID по названию — вход в Apple Account не нужен.
python3 ipa_restore.py search "Почта России" --country ru

# Восстановить по ID, ссылке App Store или bundle ID приложения.
python3 ipa_restore.py restore 771700826
python3 ipa_restore.py restore 'https://apps.apple.com/ru/app/id771700826'
python3 ipa_restore.py restore ru.russianpost.Russian-Post --country ru
```

Команда `login` передаёт интерактивные запросы Apple ID, пароля и кода двухфакторной аутентификации утилите ipatool. Пароль не передаётся в аргументах командной строки. Для сохранения сессии ipatool может запросить пароль связки ключей macOS. Не запускайте вход через `sudo`.

Перед первым входом скрипт подготавливает Unicorn 2.1.4 — компонент ipatool. Загрузка с официального PyPI продолжается после обрыва, а архив и библиотека проверяются по SHA-256 из [исходников ipatool 2.7.0](https://github.com/majd/ipatool/blob/v2.7.0/internal/sap/unicorn/artifact.go). Python-пакеты не устанавливаются. Подготовить компонент отдельно можно командой:

```sh
python3 ipa_restore.py prepare-runtime
```

`setup` загружает закреплённые версии [ipatool 2.7.0](https://github.com/majd/ipatool) и [go-ios 1.3.2](https://github.com/danielpaulus/go-ios) в `.ipa-restore/bin`, проверяет SHA-256 и извлекает только исполняемые файлы. Их лицензии — MIT. Бинарные файлы в репозитории не хранятся.

## Дополнительные команды

```sh
# Скачать IPA без телефона и установки.
python3 ipa_restore.py restore 771700826 --download-only --output ./RussianPost.ipa

# Проверить IPA или установить уже скачанный файл.
python3 ipa_restore.py inspect ./RussianPost.ipa
python3 ipa_restore.py install ./RussianPost.ipa

# Посмотреть подключённые телефоны и выбрать нужный.
python3 ipa_restore.py devices
python3 ipa_restore.py restore 771700826 --udid YOUR_DEVICE_ID

# Получить открытые сведения о приложении.
python3 ipa_restore.py lookup 771700826 --country ru

# Запросить конкретную версию, если Apple ещё её выдаёт.
python3 ipa_restore.py restore 771700826 --external-version-id VERSION_ID --output ./older.ipa
```

Если подключён один iPhone, скрипт выбирает его автоматически. При нескольких телефонах он попросит указать `--udid`. До скачивания программа проверяет подключение, а ранее скачанную IPA — на соответствие запрошенному ID. Прогресс скачивания и установки выводится в stderr; итоговая сводка JSON — в stdout. Скачивание можно встроить в Python-проект через функции `download_app`, `resolve_app_id` и `inspect_ipa`, установку — через `install_app`.

## Ограничения и безопасность

- IPA скачивается по уже имеющейся лицензии Apple. Скрипт не покупает приложения и не пытается получить новые лицензии.
- Для установки приложение обновляется по bundle ID. Скрипт не удаляет приложение и его данные; обновление может изменить формат данных приложения.
- Успешная проверка подтверждает bundle ID, версию и `IsDemotedApp: false`; вход в приложение и его данные не проверяются.
- Apple может не выдавать IPA для сгруженного приложения. Доступность зависит от региона аккаунта, истории покупок, наличия приложения в магазине и версии iOS.
- Обычное восстановление получает актуальную версию из App Store; она может отличаться от той, что стояла раньше.
- Скрипт использует неофициальные интерфейсы Apple и установки по USB; изменения на стороне Apple могут повлиять на работу.

Если появится `license required`, войдите в тот Apple Account, на который приложение было получено. Если USB-доступ не работает, разблокируйте iPhone, проверьте кабель и доверие этому компьютеру. Для некоторых операций на iOS 17 и новее go-ios может потребоваться туннель — инструкции есть в [документации go-ios](https://github.com/danielpaulus/go-ios).

Не публикуйте папку `.ipa-restore/`, скачанные IPA и файлы сессии. Репозиторий содержит только исходный код, документацию и тесты.

## Разработка

```sh
python3 -m unittest -v
python3 ipa_restore.py --help
```

Тесты не требуют Apple Account и телефона. Они проверяют разбор ID, повреждённые и неверные IPA, проверку контрольных сумм, выбор устройства и результат установки. Полный сценарий с обычной Apple-сессией успешно проверен на реальном iPhone: загрузка «Почты России» (ID 771700826), установка версии 7.8.6 и подтверждение `IsDemotedApp: false`. Apple вернул `purchased: false`.

Лицензия MIT. Предложения по улучшению приветствуются; основной скрипт остаётся одним файлом.
