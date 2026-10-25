# MarkFlow Draft — черновик для Premiere Pro по сценарию

Бесплатный скилл для нейросети из [MarkFlow](https://artemsytnik.com/markflow/). Вы даёте расшифровку (SRT)
и сценарий говорящей головы. Скилл убирает неудачные дубли, фальстарты и длинные паузы, оставляет последний
удачный дубль каждой фразы и отдаёт секвенцию для импорта в Premiere Pro.

Это не нейросеть, а скрипт: слова расшифровки сверяются со сценарием по правилам. Часовая запись
обрабатывается за секунды. Нейросеть только запускает его и объясняет результат, поэтому подойдёт любая,
которая умеет запускать код: Claude, ChatGPT, Gemini, Cursor, Codex и другие.

**[Скачать markflow-draft.zip](https://artemsytnik.com/skill/download?src=github-readme)** — архив
со страницы [artemsytnik.com/markflow](https://artemsytnik.com/markflow/), там же пошаговая инструкция.

## Как подключить

**Claude (claude.ai или приложение).** Один раз: Настройки → Возможности → Скиллы → «Загрузить скилл» →
zip. Дальше просто пишите в любом чате: «Почисти дубли по сценарию» и прикладывайте файлы.

**Claude Code, Cursor, Codex и другие агенты.** Положите папку `markflow-draft` в папку скиллов
(для Claude Code: `C:\Users\<вы>\.claude\skills\`) или просто в папку проекта.

**ChatGPT, Gemini и любой другой чат.** Каждый раз прикладывайте zip вместе с файлами и вставьте:

> Это скилл MarkFlow Draft. Распакуй архив, прочитай markflow-draft/SKILL.md и сделай по нему черновик
> для Premiere из моей расшифровки и сценария.

## Что дать нейросети

1. **SRT записи.** Premiere: правый клик по исходнику → «Новая секвенция из клипа» → окно «Текст» →
   «Расшифровать» → «Создать субтитры» → меню «···» → «Экспорт в SRT».
2. **Сценарий** — текстом, `.txt` или `.docx`. Можно без него: тогда уберутся только повторы и фальстарты.
3. **Имя файла видео**, лучше полный путь, и частоту кадров секвенции.

## Что получите

- `.xml` — секвенция для Premiere: **Файл → Импорт**;
- `.edl` — запасной вариант;
- `.srt` — субтитры по черновику;
- `_report.txt` — что вырезано и почему.

## Статистика

При каждом запуске скрипт отправляет на artemsytnik.com анонимные числа: режим (со сценарием или без),
длину записи и черновика в минутах, число кусков, версию. Текст, имена файлов и видео никуда не уходят.
Отключить: скажите нейросети «без статистики» (флаг `--no-ping`) или задайте `MARKFLOW_NO_PING=1`.

## Полная версия MarkFlow

Для студий и монтажёров: лайвы по ссылкам из сценария (скачать и поставить), настоящий проект Premiere
в стиле вашего канала, звук по вашей цепочке, музыка, звуки, несколько исходников, часовые выпуски.

[artemsytnik.com/markflow](https://artemsytnik.com/markflow/) · [Telegram @artem_sytnik1](https://t.me/artem_sytnik1) ·
[WhatsApp](https://wa.me/48572566463) · artemsytnik.forwork@gmail.com

Автор идеи — Artem Sytnik, монтажёр выпусков varlamov и Макашенца. Бесплатно для личных и рабочих проектов.

---

**English.** MarkFlow Draft is a free AI skill (Claude, ChatGPT, Gemini, Cursor, Codex) that turns a raw
talking-head recording into a rough cut for Adobe Premiere Pro: it removes failed takes, false starts and long
pauses using an SRT transcript and an optional script, and outputs FCP XML, EDL and SRT. Rule-based script,
no AI inside, Python standard library only. Full version: [artemsytnik.com/en/markflow](https://artemsytnik.com/en/markflow/).
