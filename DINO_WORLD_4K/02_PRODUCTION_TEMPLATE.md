# DINO WORLD 4K: шаблон виробництва фільму

Як знімається кожен фільм. Шаблони англійською — для копіювання; пояснення українською.

---

## 1. Конвеєр (етапи і точки погодження)

| № | Етап | Результат | Погодження |
|---|---|---|---|
| 0 | Валідація теми | картка теми, оцінка 1–100 (ваги в `03_STRATEGY.md`), приклади конкурентів з джерелом цифр | ✅ ти |
| 1 | Наукове дослідження | `research.md`: період, регіон, клімат, рослини, картки видів, джерела, мітки FACT/INTERP/HYPO | — |
| 2 | Концепція і структура | логлайн, центральне питання, акти й епізоди, зустрічі, полювання, хронометраж | ✅ ти |
| 3 | Production bible фільму | вигляд кожного виду й особини (reference sheets), локації, палітра, правила камери і звуку | ✅ ти |
| 4 | Сценарій | `documentary_script.md`: текст диктора англійською з таймінгом | ✅ ти |
| 5 | Шот-лист | `scene_breakdown.md`: картки сцен (шаблон §4) + журнал безперервності | — |
| 6 | Тестові сцени | 3 тести у Flow: POV-ландшафт, зустріч, полювання → оцінка якості | ✅ ти |
| 7 | Виробництво ассетів | промти Flow, сток, кадри, графіка; таблиця `VIDEO_LIBRARY` | — |
| 8 | Озвучка | WAV по сегментах за постійним профілем голосу; QC | — |
| 9 | Монтаж | збірка → чорновий → чистовий; колір, звук, музика, майстеринг | ✅ ти (чорновий) |
| 10 | Контроль якості | чек-лист §11 | — |
| 11 | Упаковка | 3 назви, 3 обкладинки, опис, глави, теги, закріплений коментар, кінцева заставка, Shorts | ✅ ти |
| 12 | Публікація й аналіз | CTR, середній час перегляду, графік утримання → висновки в `youtube/performance_tracking.md` | — |

---

## 2. Типи матеріалу

| Код | Тип | Для чого |
|---|---|---|
| **A** | Google Flow / Veo | зустрічі, рух тварин, полювання, POV, усе, де важлива конкретна тварина і безперервність |
| **B** | Сток | загальні плани, вода, туман, небо, рослини зблизька (після наукового фільтра) |
| **C** | Гібрид | сток-фон + AI-тварина або AI-доробка стоку |
| **D** | Статичні кадри й графіка | DATABASE ACTIVATED, інфографіка, мапи, схеми скелетів, порівняння з людиною (1,75 м) |

---

## 3. Розрахунки (заповнювати для кожного фільму)

**Обсяг тексту диктора:**
`слова = хвилини × частка_голосу × темп`
Приклад: 50 хв × 0,55 × 140 слів/хв ≈ 3 850 слів (на епізод 16 хв ≈ 1 250). Реальний темп голосу заміряємо на прослуховуванні.

**Кількість AI-генерацій:**
`придатні_кліпи = (хронометраж_с × частка_A) / придатні_секунди_з_кліпу`
`генерації = придатні_кліпи × спроби (1,5–2,5)`
Приклад для 50 хв (3 000 с): D ≈ 20% (600 с), B ≈ 25% (750 с), A + C ≈ 55% (1 650 с). При 6 придатних секундах з 8-секундного кліпу виходить ≈ 275 кліпів, тобто ≈ 400–550 генерацій. На один епізод 16 хв — приблизно третина.

**Графік:** `тижні = генерації / генерацій_на_тиждень_за_твоїм_тарифом`. Тому квоту Flow треба знати до старту.

---

## 4. Картка сцени (шот-лист)

```markdown
### SCENE D01-S023 — "First tracks on the riverbank"
- Part / Episode: PART 1 SUNRISE / EP1
- Type: A (Flow) | B | C | D
- Duration (edit): 6 s  | Source clip: 8 s
- Location: LOC-02 River sandbar (see bible)
- Time / Weather / Light: 06:40, mist lifting, low warm sun from camera-left
- Narration (EN): "The mud remembers everything. These tracks are fresh."
- Science tags: [FACT] hadrosaur trackways known from the region; [RECON] exact scene
- Visual: POV looking down at three-toed tracks filling with water, ferns at frame edge
- Camera: POV, eye height 1.7 m, slow tilt down, gentle handheld sway
- Creatures: none visible (sound only: distant low boom)
- Sound: river, insects, distant closed-mouth boom, footsteps in mud
- Transition IN: continues from S022 (traveler stepping out of ferns onto sand)
- Transition OUT: camera tilts up → S024 reveals herd in the shallows
- Continuity state at END: camera at sandbar edge facing east; sun camera-left; mist thin
```

## 5. Журнал безперервності (continuity ledger)

Перед кожним наступним промтом заповнюємо стан кінця попередньої сцени. Ці дані дослівно переходять у новий промт.

```markdown
| Scene | Camera position & facing | Creature positions & heading | Light / sun direction | Weather | Objects in frame | Next action |
|---|---|---|---|---|---|---|
| S023 | sandbar edge, facing east, eye 1.7 m | herd 40 m ahead in shallows, heading north | low sun camera-left, warm | mist thinning | tracks, fern clump right | tilt up to reveal herd |
```

**Прийом проти «стрибків»:** якщо твій тариф Flow має режим Frames to Video або Extend, **останній кадр попереднього кліпу = перший кадр наступного**. Це найнадійніший спосіб зберегти безперервність (доступність режиму перевіримо).

## 6. Reference sheet тварини (еталон особини)

```markdown
### REX-01 — "Scar" (adult female Tyrannosaurus rex)
- Size: about 12 m long, about 3.7 m at the hips, very massive build
- Head: deep skull, forward-facing eyes, LIPS covering the teeth when the mouth is closed,
  small hornlets above the eyes, healed diagonal scar across the left upper jaw
- Skin: fine pebbly scales; dark olive-brown back fading to dull tan belly; faint darker
  bands on the tail (colour = reconstruction)
- Arms: short, TWO fingers per hand, palms facing inward (never palms-down)
- Legs/feet: powerful, three weight-bearing toes, heavy foot pads
- Tail: thick, held level, counterbalancing the head
- Movement: slow, heavy, deliberate; long strides; head held level; never jumps
- Reference image: assets/ref/REX-01.png
```

## 7. Шаблон промту Google Flow / Veo (англійською)

Кожен промт самодостатній: жодних «як у попередній сцені». Розрахований на 8-секундний кліп.

```text
[SCENE ID] D01-S024 — Herd reveal at the river

[HISTORICAL SETTING]
Late Cretaceous, about 66 million years ago, Hell Creek river valley (present-day
Montana, USA). Warm, humid early morning, about 06:45.

[ENVIRONMENT]
Wide, slow brown river with sandbars. Far bank: dense forest of flowering trees and
conifers, ferns and low shrubs at the waterline. NO grass, no meadows. Mist lifting
off the water. Soft low sun from camera-left, warm golden light, long shadows.

[CREATURES]
Herd of 9 Edmontosaurus annectens wading in the shallows 30–50 m away. Each about
11–12 m long, broad flat duck-like beak, heavy body, standing on four limbs while
wading, grey-green pebbly skin with lighter belly, no feathers. Adults and two
smaller juveniles in the middle of the group.

[ANATOMY CORRECTIONS]
Realistic proportions, correct number of limbs and digits, no extra heads, no
duplicated animals merging together, no Jurassic Park styling.

[CAMERA / POV]
First-person POV of a human traveler standing on the sandbar edge, eye height 1.7 m.
Camera slowly tilts up from wet sand to the river and settles on the herd. Subtle
natural handheld sway. 35 mm lens look, deep focus. POV is kept for the whole shot.

[ACTION — SECOND BY SECOND]
0–2 s: camera looks down at fresh three-toed tracks filling with water.
2–5 s: smooth tilt up across the water; the herd comes into view through the mist.
5–8 s: an adult Edmontosaurus lifts its head from the water, water dripping from the
beak, and looks toward camera; the rest keep feeding.

[PHYSICS]
Heavy bodies displace water in slow ripples; dripping water; mist drifts slowly;
tracks have water seeping in; nothing floats or moves unnaturally.

[LIGHTING & CINEMATOGRAPHY]
Golden-hour backlight with rim light on the animals, atmospheric haze adds depth,
natural colour, wildlife documentary realism.

[SOUND]
River flow, insects, distant low closed-mouth booming calls, soft footsteps in mud.
No music, no human voice.

[QUALITY]
Photorealistic cinematic wildlife documentary, realistic animal anatomy, physically
accurate movement, detailed skin texture, natural lighting, 16:9.

[NEGATIVE]
No cartoon style, no fantasy creatures, no feathers on Edmontosaurus, no grass, no
modern plants or animals, no humans, hands or body parts in frame, no buildings,
no text or logos, no sudden camera cuts, no teleporting, no changes in animal
appearance, no inconsistent shadows.
```

**Блок Anatomy Corrections за видами** (додавати під конкретну тварину):
- **T. rex:** lips covering teeth when mouth closed; TWO fingers per hand; palms facing inward; level head and tail; no feathers on adults except possibly sparse filaments (avoid in v1); no roaring with mouth wide open.
- **Triceratops:** three facial horns (two long brow horns + short nose horn); solid frill with small bony knobs on the edge; large polygonal scales; walks on four legs.
- **Edmontosaurus:** broad flat toothless beak front; no head crest; may stand on four legs or two.
- **Dromaeosaurids (Acheroraptor, Dakotaraptor):** fully feathered with wing-like arm feathers; enlarged sickle claw on second toe held raised; NOT scaly; palms facing inward.

## 8. Запит на сток (тип B)

```markdown
- Scene: D01-S019
- Search query (EN): "misty river sunrise forest aerial no people"
- Needed shot: wide aerial over slow river at dawn, dense forest, mist layers
- Duration: ≥ 8 s | Format: ≥ 1080p, 16:9, ≥ 24 fps
- Must match: warm low sun from left, mist, no grass meadows, no roads/buildings/birds
- Source / link: …
- License (checked on date): … | Commercial: yes/no | Attribution: yes/no
```

## 9. Фірмовий сегмент DINOSAUR DATABASE ACTIVATED

| Крок | Що відбувається | Тривалість | Матеріал |
|---|---|---|---|
| 1 Encounter | тварина з'являється, найцікавіший момент | 4–8 с | A |
| 2 Freeze | різкий стоп-кадр + низький звуковий удар | 0,3 с | D (кадр з A) |
| 3 Activated | напис DINOSAUR DATABASE ACTIVATED | ~1 с | D |
| 4 Letters | T — R — I — C — E — R — A — T — O — P — S, звук на кожну букву | 0,12–0,18 с на букву | D |
| 5 Name | повна назва + наукова назва дрібніше | 1,5 с | D |
| 6 Profile | розповідь диктора: період, місце, розмір, їжа, захист, факти, невизначеність | 30–60 с | D: стоп-кадр з повільним рухом, силует з людиною 1,75 м, мапа, інфографіка |
| 7 Return | інтерфейс зникає, кадр «розморожується», звуки природи повертаються | 1 с | A |

Правила: повний ритуал — для перших 2–3 зустрічей фільму, далі скорочена версія (без буквенної анімації), щоб не втомлювати глядача. Характеристики не зачитуємо списком: це історія. Сегмент дає 0 генерацій відео, а ще з нього легко зробити Shorts «What dinosaur is this?».

## 10. Сцена полювання

Структура: DISCOVERY → STALKING → TENSION → ATTACK → CHASE → STRUGGLE → RESULT.
Перед написанням перевіряємо: хижак і здобич співіснували; можливості хижака; захист здобичі; є докази (сліди укусів, загоєні травми) чи це гіпотеза.
Для кожного 8-секундного блоку: позиції обох тварин, відстань, напрямок, місцевість, швидкість, реакція, камера, звук. Не кожне полювання закінчується вбивством. Без натуралістичної жорстокості: напруга, а не кров.

## 11. Чек-лист контролю якості (перед показом)

- [ ] **Наука:** кожна тварина з цього часу й місця; мітки FACT/INTERP/HYPO в робочому тексті; розміри діапазонами.
- [ ] **Зовнішність:** особини не змінюються між сценами (колір, шрами, розмір).
- [ ] **Час і місце:** немає змішування епох і континентів.
- [ ] **Камера:** POV збережено; немає випадкових рук і ніг.
- [ ] **Дія:** наступний кліп продовжує попередній, без дублювання останніх секунд.
- [ ] **Фізика:** маса, інерція, контакт із землею, вода, пил.
- [ ] **Звук:** відповідає подіям; вигадані звуки не подано як факт.
- [ ] **Утримання:** кожні 2–3 хв нова подія; немає провисань.
- [ ] **Технічне:** без watermark і чужих логотипів; роздільність і fps однакові; −14 LUFS, true peak ≤ −1 dBTP.
- [ ] **Ліцензії:** кожен файл записано в таблицю з ліцензією і датою перевірки.

## 12. Монтаж і майстеринг

- Таймлайн: 3840×2160, 24 або 25 fps (одна частота на весь фільм), аудіо 48 кГц / 24 біт.
- Колір: спершу вирівнюємо експозицію і баланс, потім один спільний grade на весь фільм.
- Голос: легкий EQ, де-есер, м'яка компресія; ducking музики −8…−12 dB під голосом.
- Експорт: H.264/H.265 високого бітрейту або ProRes-проміжний → завантаження у 4K.

## 13. Імена файлів і папки

`D01_S023_A_v02_veo.mp4` = документалка 01, сцена 023, тип A, версія 2, джерело veo.
Озвучка: `D01_VO_S023_v01.wav`. Графіка: `D01_GFX_DB_TRICERATOPS_v01.mov`.

```
documentaries/01_one_day_in_the_world_of_dinosaurs/
  concept.md  research.md  bible.md  documentary_script.md  scene_breakdown.md
  image_prompts.md  flow_prompts.md  stock_requests.md  editing_plan.md
  assets/ref/  footage/A_flow/  footage/B_stock/  footage/C_hybrid/  graphics/
  narration/  music/  sound_effects/  final/
```
(Великі медіафайли в git не комітимо, лише текстові документи й таблиці.)

## 14. Упаковка для YouTube

**Назви:** мінімум 3 варіанти. Curiosity gap + правда про зміст + до ~60 символів.
**Обкладинка:** один головний об'єкт, сильний контраст, емоція, 0–3 слова тексту, читається на телефоні. 3 концепції перед вибором.

**Шаблон опису:**
```text
{HOOK — 1–2 sentences restating the central question}

{2–3 sentences: what the viewer will experience and learn}

🔔 Subscribe to DINO WORLD 4K for cinematic prehistoric documentaries:
https://www.youtube.com/@{HANDLE}?sub_confirmation=1

⏱ CHAPTERS
00:00 {Cold open}
…

🔬 SCIENCE NOTE
The animals in this film are reconstructions based on fossil evidence and current
research. Where scientists still disagree, the narration says so. Key sources:
- {Author, year, journal / museum}

🎬 PRODUCTION NOTE
Visual reconstructions in this documentary were created with AI-assisted tools and
edited by the DINO WORLD 4K team.

#Dinosaurs #PrehistoricLife #Documentary
```

**Також:** глави (00:00 обов'язково), 10–15 тегів, 3 хештеги, закріплений коментар з питанням до глядачів, кінцева заставка 20 с (Subscribe + наступне відео), брендовий watermark у Studio, мітка synthetic content, 2–3 Shorts як самостійні міні-історії.
