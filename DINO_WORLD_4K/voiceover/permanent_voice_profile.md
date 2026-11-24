# Постійний профіль голосу DINO WORLD 4K

**Статус: НЕ ОБРАНО — чекає прослуховування.**
Цей файл читається перед кожним виробництвом. Поки статус не SELECTED, фінальну озвучку не генеруємо.

---

## 1. Двигун

- **Kokoro-82M** (hexgrad). Ліцензія моделі й голосів: **Apache-2.0**, комерційне використання дозволене. Перевірити ще раз на сторінці моделі в день завантаження і записати версію / хеш файлу сюди.
- Сам аудіовихід не підпадає під GPL-ліцензію espeak-ng (використовується лише як інструмент фонемізації). Ми розповсюджуємо аудіо, а не програму.
- Нативна частота Kokoro — 24 кГц. Регулювання висоти тону (pitch) немає; зсув тону в постобробці погіршує природність, тому pitch = 0.

**Блокер у хмарному середовищі:** `huggingface.co` заблоковано мережевою політикою, тому ваги моделі тут не завантажуються (PyPI доступний). Варіанти: дозволити хост у налаштуваннях середовища або запустити локально.

## 2. Кандидати

| Голос | Акцент | Примітка |
|---|---|---|
| am_michael | американський | основний кандидат з брифу |
| am_fenrir | американський | основний кандидат з брифу |
| bm_george | британський | основний кандидат з брифу; британський акцент асоціюється з природничою документалістикою |
| bm_lewis, am_onyx | — | необов'язково, для порівняння |

## 3. Протокол прослуховування

1. Один і той самий текст (нижче) для всіх голосів, speed 0,90 / 0,95 / 1,00.
2. Текст ділимо на речення-сегменти; паузи між сегментами додаємо в монтажі (0,4–1,2 с).
3. Оцінка 1–10 за критеріями: природність, глибина і «кінематографічність», вимова наукових назв, емоційність, паузи, комфорт на довгому прослуховуванні (слухати 3 хв підряд).
4. Ти слухаєш і обираєш. Асистент без прослуховування не обирає.

### Тестовий текст (~60 секунд)

```text
Sixty-six million years ago, the sun rose over a world that no longer exists.

Mist hangs above the river. The forest is quiet. Too quiet.

Somewhere beyond the ferns, something enormous is moving.

This is Hell Creek — a lush river valley in what will one day become Montana.

Herds of Edmontosaurus wade through the shallows. A lone Triceratops tears at the
undergrowth, its three horns catching the first light.

But every creature here shares the same instinct. Listen.

Because this valley belongs to the most famous predator in Earth's history:
Tyrannosaurus rex.

Scientists have studied its bones for more than a century. Yet some of its secrets
are only being uncovered today.

Today, we won't just study this world. We will walk through it — from sunrise,
to nightfall.
```

### Перевірка вимови

```text
Pachycephalosaurus. Thescelosaurus. Ankylosaurus. Acheroraptor. Dakotaraptor.
Nanotyrannus. Maastrichtian. Cretaceous. Didelphodon. Champsosaurus.
```
Неправильні вимови фіксуємо в `pronunciation_dictionary.md` (фонетичне написання для Kokoro).

## 4. Параметри (заповнити після вибору)

| Параметр | Значення |
|---|---|
| Двигун і версія | Kokoro-82M, v… (хеш …) |
| Голос | … |
| Мова / акцент | en-US або en-GB |
| Speed | … |
| Pitch | 0 (без зміни) |
| Нативний вихід | WAV 24 кГц |
| Майстер для монтажу | WAV 48 кГц / 24 біт (ресемплінг лише для таймлайна) |
| Паузи | коротка 0,4 с, середня 0,8 с, драматична 1,2–1,5 с (в монтажі) |
| Обробка | high-pass ~80 Гц, де-есер, м'яка компресія, без кліпінгу |
| Гучність | голос ~−16…−14 LUFS перед зведенням; фінал фільму −14 LUFS, true peak ≤ −1 dBTP |

## 5. Виключені варіанти і чому

- **XTTS-v2 (Coqui):** ліцензія CPML лише для некомерційного використання, а компанія закрилася, тож комерційну ліцензію купити ніде.
- **edge-tts:** неофіційний доступ до сервісу Microsoft; ризик порушення умов для монетизованого каналу.
- **Piper:** допускається лише голос з перевіреною дозвільною ліцензією (у кожного голосу своя).
- **Голоси F5-TTS:** ваги CC-BY-NC, некомерційні.
