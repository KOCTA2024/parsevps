
## Базові поля матчу

| Ключ  | Повна назва       | Опис                                                  |
|-------|-------------------|-------------------------------------------------------|
| `src` | Source            | Мітка секції: `MAIN MATCH`, `TeamName (Recent)`, `H2H`, або роздільник `--- ... ---` |
| `mid` | Match_ID          | Унікальний ідентифікатор матчу (Flashscore)           |
| `dt`  | Date              | Дата та час матчу (рядок, локаль ru-RU)               |
| `st`  | Status            | Статус: `Finished`, `Not Started`, `Live (Q2 18')` тощо |
| `tour`| Tournament        | Назва турніру; може містити інфо про серію через ` \| ` |
| `ht`  | Home_Team         | Назва домашньої команди                               |
| `at`  | Away_Team         | Назва гостьової команди                               |
| `hs`  | Home_Score        | Фінальний або поточний рахунок домашньої команди      |
| `as_` | Away_Score        | Фінальний або поточний рахунок гостьової команди      |
| `tot` | Total_Score       | Загальний рахунок (`hs + as_`)                        |
| `url` | URL               | Посилання на сторінку матчу на flashscore.com         |

---

## Рахунок по чвертях / овертаймах

Суфікси: `h` = home (хазяї), `a` = away (гості), `t` = total (сума).

| Ключ   | Повна назва   | Ключ   | Повна назва   | Ключ   | Повна назва    |
|--------|---------------|--------|---------------|--------|----------------|
| `q1h`  | Q1_Home       | `q1a`  | Q1_Away       | `q1t`  | Q1_Total       |
| `q2h`  | Q2_Home       | `q2a`  | Q2_Away       | `q2t`  | Q2_Total       |
| `q3h`  | Q3_Home       | `q3a`  | Q3_Away       | `q3t`  | Q3_Total       |
| `q4h`  | Q4_Home       | `q4a`  | Q4_Away       | `q4t`  | Q4_Total       |
| `ot1h` | OT1_Home      | `ot1a` | OT1_Away      | `ot1t` | OT1_Total      |
| `ot2h` | OT2_Home      | `ot2a` | OT2_Away      | `ot2t` | OT2_Total      |

---

## Статистика — суфікси секцій

| Суфікс в ключі | Секція              |
|----------------|---------------------|
| `m`            | Match (весь матч)   |
| `1`            | Q1 (1-а чверть)     |
| `2`            | Q2 (2-а чверть)     |
| `3`            | Q3 (3-я чверть)     |
| `4`            | Q4 (4-а чверть)     |

Префікс `h` = home, `a` = away.

---

## Статистика — весь матч (`_m`)

| Ключ    | Повна назва              | Опис                          |
|---------|--------------------------|-------------------------------|
| `hfgam` | Home_FGA_Match           | Спроби з гри (хазяї)          |
| `afgam` | Away_FGA_Match           | Спроби з гри (гості)          |
| `hfgmm` | Home_FGM_Match           | Влучання з гри (хазяї)        |
| `afgmm` | Away_FGM_Match           | Влучання з гри (гості)        |
| `hfgpm` | Home_FG_Pct_Match        | % влучань з гри (хазяї)       |
| `afgpm` | Away_FG_Pct_Match        | % влучань з гри (гості)       |
| `h2pam` | Home_2P_Att_Match        | 2-очкові спроби (хазяї)       |
| `a2pam` | Away_2P_Att_Match        | 2-очкові спроби (гості)       |
| `h2pmm` | Home_2P_Made_Match       | 2-очкові влучання (хазяї)     |
| `a2pmm` | Away_2P_Made_Match       | 2-очкові влучання (гості)     |
| `h2ppm` | Home_2P_Pct_Match        | % 2-очкових (хазяї)           |
| `a2ppm` | Away_2P_Pct_Match        | % 2-очкових (гості)           |
| `h3pam` | Home_3P_Att_Match        | 3-очкові спроби (хазяї)       |
| `a3pam` | Away_3P_Att_Match        | 3-очкові спроби (гості)       |
| `h3pmm` | Home_3P_Made_Match       | 3-очкові влучання (хазяї)     |
| `a3pmm` | Away_3P_Made_Match       | 3-очкові влучання (гості)     |
| `h3ppm` | Home_3P_Pct_Match        | % 3-очкових (хазяї)           |
| `a3ppm` | Away_3P_Pct_Match        | % 3-очкових (гості)           |
| `hftam` | Home_FT_Att_Match        | Штрафні спроби (хазяї)        |
| `aftam` | Away_FT_Att_Match        | Штрафні спроби (гості)        |
| `hftmm` | Home_FT_Made_Match       | Штрафні влучання (хазяї)      |
| `aftmm` | Away_FT_Made_Match       | Штрафні влучання (гості)      |
| `hftpm` | Home_FT_Pct_Match        | % штрафних (хазяї)            |
| `aftpm` | Away_FT_Pct_Match        | % штрафних (гості)            |
| `hrbm`  | Home_Rebounds_Match      | Підбирання всього (хазяї)     |
| `arbm`  | Away_Rebounds_Match      | Підбирання всього (гості)     |
| `horbm` | Home_Off_Rebounds_Match  | Підбирання у нападі (хазяї)   |
| `aorbm` | Away_Off_Rebounds_Match  | Підбирання у нападі (гості)   |
| `hdrbm` | Home_Def_Rebounds_Match  | Підбирання у захисті (хазяї)  |
| `adrbm` | Away_Def_Rebounds_Match  | Підбирання у захисті (гості)  |
| `hastm` | Home_Assists_Match       | Передачі (хазяї)              |
| `aastm` | Away_Assists_Match       | Передачі (гості)              |
| `hstlm` | Home_Steals_Match        | Перехоплення (хазяї)          |
| `astlm` | Away_Steals_Match        | Перехоплення (гості)          |
| `hblkm` | Home_Blocks_Match        | Блоки (хазяї)                 |
| `ablkm` | Away_Blocks_Match        | Блоки (гості)                 |
| `htovm` | Home_Turnovers_Match     | Втрати (хазяї)                |
| `atovm` | Away_Turnovers_Match     | Втрати (гості)                |
| `hflsm` | Home_Fouls_Match         | Персональні фоли (хазяї)      |
| `aflsm` | Away_Fouls_Match         | Персональні фоли (гості)      |

---

## Статистика по чвертях — паттерн ключів

Для кожної чверті (Q1–Q4) ключі будуються за шаблоном:

```
h{stat}{n}  →  Home_{Stat}_Q{n}
a{stat}{n}  →  Away_{Stat}_Q{n}
```

Де `{n}` = 1, 2, 3 або 4, а `{stat}`:

| Частина ключа | Статистика              |
|---------------|-------------------------|
| `rb`          | Rebounds (підбирання)   |
| `orb`         | Off_Rebounds (напад)    |
| `drb`         | Def_Rebounds (захист)   |
| `ast`         | Assists (передачі)      |
| `stl`         | Steals (перехоплення)   |
| `blk`         | Blocks (блоки)          |
| `tov`         | Turnovers (втрати)      |
| `fls`         | Fouls (фоли)            |
| `fga`         | FGA (спроби з гри)      |
| `fgm`         | FGM (влучання з гри)    |
| `fgp`         | FG_Pct (% з гри)        |
| `2pa`         | 2P_Att (2-очк. спроби)  |
| `2pm`         | 2P_Made (2-очк. влуч.)  |
| `2pp`         | 2P_Pct (% 2-очк.)       |
| `3pa`         | 3P_Att (3-очк. спроби)  |
| `3pm`         | 3P_Made (3-очк. влуч.)  |
| `3pp`         | 3P_Pct (% 3-очк.)       |
| `fta`         | FT_Att (штр. спроби)    |
| `ftm`         | FT_Made (штр. влуч.)    |
| `ftp`         | FT_Pct (% штрафних)     |

