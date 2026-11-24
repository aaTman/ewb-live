# visual-tracks repro sweep — every TC/MLC event at (a) mid-life, (b) first frame after max(last fix, published end), (c) window end

Data: data/results (schema 3, generated 2026-11-19T15:00:50+00:00 — published build copied read-only from the box). Slider frames: 2026082100..2026111900 (361 steps; extended 6-hourly past the ERA5 cutoff to the window end, exactly like mergedmap._build).
Events swept: 124 (44 TC, 80 MLC); ongoing: 1. Contexts: global + TC tab + MLC tab + 2 region views.

Rule under test: for frames t > max(last fix, published end) (t > last fix when life.ongoing) the map must draw NOTHING for the event: no SVG mark/ring/label, no track pixels (`hits` = 5x5 probes on #gm-tracks at the event's own fix positions), no wind field.

## Violations — something still drawn past the cap

| ctx | event | type | ongoing | last fix | end | cap | frame | marks | labels | canvas hits | field |
|---|---|---|---|---|---|---|---|---|---|---|---|
| global | 68ec790015c7 | tropical_cyclone | False | 08-25 18:00 | 08-25 21:00 | 08-25 21:00 | 2026082600 | 0 | 0 | 2/39 | False |
| global | e57f7ebfdf54 | midlat_cyclone | False | 08-25 12:00 | 08-26 00:00 | 08-26 00:00 | 2026082606 | 0 | 0 | 1/9 | False |
| global | 95180b67568a | tropical_cyclone | False | 08-27 00:00 | 08-27 00:00 | 08-27 00:00 | 2026082706 | 0 | 0 | 3/45 | False |
| global | 06be9280df4b | midlat_cyclone | False | 08-30 18:00 | 08-31 00:00 | 08-31 00:00 | 2026083106 | 0 | 0 | 1/17 | False |
| global | 5abc5a4b6214 | midlat_cyclone | False | 08-31 06:00 | 09-01 00:00 | 09-01 00:00 | 2026090106 | 0 | 0 | 8/11 | False |
| global | 51a6978b7d36 | midlat_cyclone | False | 09-01 06:00 | 09-02 00:00 | 09-02 00:00 | 2026090206 | 0 | 0 | 1/16 | False |
| global | 71cbd7eeaf25 | midlat_cyclone | False | 09-02 06:00 | 09-02 00:00 | 09-02 06:00 | 2026090212 | 0 | 0 | 3/10 | False |
| global | 8f747eb0052d | tropical_cyclone | False | 09-07 00:00 | 09-07 03:00 | 09-07 03:00 | 2026090706 | 0 | 0 | 1/105 | False |
| global | a966ddfde13c | tropical_cyclone | False | 09-08 12:00 | 09-08 15:00 | 09-08 15:00 | 2026090818 | 0 | 0 | 14/79 | False |
| global | 3684807ce119 | midlat_cyclone | False | 09-08 06:00 | 09-09 00:00 | 09-09 00:00 | 2026090906 | 0 | 0 | 2/13 | False |
| global | 3006ff3558ba | tropical_cyclone | False | 09-15 12:00 | 09-14 21:00 | 09-15 12:00 | 2026091518 | 0 | 0 | 28/63 | False |
| global | 21f32fd04468 | midlat_cyclone | False | 09-15 12:00 | 09-16 00:00 | 09-16 00:00 | 2026091606 | 0 | 0 | 2/18 | False |
| global | de869c0ce1f4 | midlat_cyclone | False | 09-19 06:00 | 09-14 00:00 | 09-19 06:00 | 2026091912 | 0 | 0 | 3/33 | False |
| global | be183ee601c7 | tropical_cyclone | False | 09-27 18:00 | 09-27 21:00 | 09-27 21:00 | 2026092800 | 0 | 0 | 45/89 | False |
| global | dd9825d6b56e | tropical_cyclone | False | 09-30 00:00 | 09-30 00:00 | 09-30 00:00 | 2026093006 | 0 | 0 | 47/101 | False |
| global | 720d16977a23 | midlat_cyclone | False | 10-04 00:00 | 10-05 00:00 | 10-05 00:00 | 2026100506 | 0 | 0 | 8/14 | False |
| global | 47c1418e0020 | midlat_cyclone | False | 10-07 06:00 | 10-02 00:00 | 10-07 06:00 | 2026100712 | 0 | 0 | 10/29 | False |
| global | 097d779c88fd | tropical_cyclone | False | 10-11 12:00 | 10-11 12:00 | 10-11 12:00 | 2026101118 | 0 | 0 | 49/133 | False |
| global | 1a992ebc5446 | tropical_cyclone | False | 10-11 12:00 | 10-10 15:00 | 10-11 12:00 | 2026101118 | 0 | 0 | 9/249 | False |
| global | 5e360d5663b4 | tropical_cyclone | False | 10-16 18:00 | 10-16 15:00 | 10-16 18:00 | 2026101700 | 0 | 0 | 39/99 | False |
| global | a564bf02954f | tropical_cyclone | False | 10-20 00:00 | 10-20 00:00 | 10-20 00:00 | 2026102006 | 0 | 0 | 8/71 | False |
| global | 47aeeb1f9b54 | midlat_cyclone | False | 10-20 06:00 | 10-21 00:00 | 10-21 00:00 | 2026102106 | 0 | 0 | 4/10 | False |
| global | f3727a8efbc6 | midlat_cyclone | False | 10-22 00:00 | 10-22 00:00 | 10-22 00:00 | 2026102206 | 0 | 0 | 2/11 | False |
| global | 9ad7027c17dc | midlat_cyclone | False | 10-24 12:00 | 10-25 00:00 | 10-25 00:00 | 2026102506 | 0 | 0 | 4/14 | False |
| global | 08fac5554fb4 | midlat_cyclone | False | 10-25 00:00 | 10-26 00:00 | 10-26 00:00 | 2026102606 | 0 | 0 | 6/11 | False |
| global | 52327cd61784 | midlat_cyclone | False | 10-26 12:00 | 10-26 00:00 | 10-26 12:00 | 2026102618 | 0 | 0 | 3/11 | False |
| global | 450bed8d1975 | midlat_cyclone | False | 10-27 06:00 | 10-25 00:00 | 10-27 06:00 | 2026102712 | 0 | 0 | 7/25 | False |
| global | 77a6e1faa78f | midlat_cyclone | False | 10-30 18:00 | 10-28 00:00 | 10-30 18:00 | 2026103100 | 0 | 0 | 2/19 | False |
| global | bf564a80ac5f | midlat_cyclone | False | 10-31 00:00 | 10-30 00:00 | 10-31 00:00 | 2026103106 | 0 | 0 | 1/17 | False |
| global | a1d6b391e4d8 | tropical_cyclone | True | 11-17 00:00 | 11-17 03:00 | 11-17 00:00 | 2026111706 | 1 | 1 | 15/15 | False |
| global | 7171619838ce | tropical_cyclone | False | 10-19 06:00 | 10-19 09:00 | 10-19 09:00 | 2026111900 | 0 | 0 | 2/25 | False |
| global | a1d6b391e4d8 | tropical_cyclone | True | 11-17 00:00 | 11-17 03:00 | 11-17 00:00 | 2026111900 | 1 | 1 | 15/15 | False |
| tab:TC | 68ec790015c7 | tropical_cyclone | False | 08-25 18:00 | 08-25 21:00 | 08-25 21:00 | 2026082600 | 0 | 0 | 2/39 | False |
| tab:TC | 95180b67568a | tropical_cyclone | False | 08-27 00:00 | 08-27 00:00 | 08-27 00:00 | 2026082706 | 0 | 0 | 3/45 | False |
| tab:TC | 8f747eb0052d | tropical_cyclone | False | 09-07 00:00 | 09-07 03:00 | 09-07 03:00 | 2026090706 | 0 | 0 | 1/105 | False |
| tab:TC | a966ddfde13c | tropical_cyclone | False | 09-08 12:00 | 09-08 15:00 | 09-08 15:00 | 2026090818 | 0 | 0 | 14/79 | False |
| tab:TC | 3006ff3558ba | tropical_cyclone | False | 09-15 12:00 | 09-14 21:00 | 09-15 12:00 | 2026091518 | 0 | 0 | 28/63 | False |
| tab:TC | be183ee601c7 | tropical_cyclone | False | 09-27 18:00 | 09-27 21:00 | 09-27 21:00 | 2026092800 | 0 | 0 | 45/89 | False |
| tab:TC | dd9825d6b56e | tropical_cyclone | False | 09-30 00:00 | 09-30 00:00 | 09-30 00:00 | 2026093006 | 0 | 0 | 47/101 | False |
| tab:TC | 097d779c88fd | tropical_cyclone | False | 10-11 12:00 | 10-11 12:00 | 10-11 12:00 | 2026101118 | 0 | 0 | 49/133 | False |
| tab:TC | 1a992ebc5446 | tropical_cyclone | False | 10-11 12:00 | 10-10 15:00 | 10-11 12:00 | 2026101118 | 0 | 0 | 9/249 | False |
| tab:TC | 5e360d5663b4 | tropical_cyclone | False | 10-16 18:00 | 10-16 15:00 | 10-16 18:00 | 2026101700 | 0 | 0 | 39/99 | False |
| tab:TC | a564bf02954f | tropical_cyclone | False | 10-20 00:00 | 10-20 00:00 | 10-20 00:00 | 2026102006 | 0 | 0 | 8/71 | False |
| tab:TC | a1d6b391e4d8 | tropical_cyclone | True | 11-17 00:00 | 11-17 03:00 | 11-17 00:00 | 2026111706 | 1 | 1 | 15/15 | False |
| tab:TC | 7171619838ce | tropical_cyclone | False | 10-19 06:00 | 10-19 09:00 | 10-19 09:00 | 2026111900 | 0 | 0 | 2/25 | False |
| tab:TC | a1d6b391e4d8 | tropical_cyclone | True | 11-17 00:00 | 11-17 03:00 | 11-17 00:00 | 2026111900 | 1 | 1 | 15/15 | False |
| tab:MLC | e57f7ebfdf54 | midlat_cyclone | False | 08-25 12:00 | 08-26 00:00 | 08-26 00:00 | 2026082606 | 0 | 0 | 1/9 | False |
| tab:MLC | 06be9280df4b | midlat_cyclone | False | 08-30 18:00 | 08-31 00:00 | 08-31 00:00 | 2026083106 | 0 | 0 | 1/17 | False |
| tab:MLC | 5abc5a4b6214 | midlat_cyclone | False | 08-31 06:00 | 09-01 00:00 | 09-01 00:00 | 2026090106 | 0 | 0 | 8/11 | False |
| tab:MLC | 51a6978b7d36 | midlat_cyclone | False | 09-01 06:00 | 09-02 00:00 | 09-02 00:00 | 2026090206 | 0 | 0 | 1/16 | False |
| tab:MLC | 71cbd7eeaf25 | midlat_cyclone | False | 09-02 06:00 | 09-02 00:00 | 09-02 06:00 | 2026090212 | 0 | 0 | 3/10 | False |
| tab:MLC | 3684807ce119 | midlat_cyclone | False | 09-08 06:00 | 09-09 00:00 | 09-09 00:00 | 2026090906 | 0 | 0 | 2/13 | False |
| tab:MLC | 21f32fd04468 | midlat_cyclone | False | 09-15 12:00 | 09-16 00:00 | 09-16 00:00 | 2026091606 | 0 | 0 | 2/18 | False |
| tab:MLC | de869c0ce1f4 | midlat_cyclone | False | 09-19 06:00 | 09-14 00:00 | 09-19 06:00 | 2026091912 | 0 | 0 | 3/33 | False |
| tab:MLC | 720d16977a23 | midlat_cyclone | False | 10-04 00:00 | 10-05 00:00 | 10-05 00:00 | 2026100506 | 0 | 0 | 8/14 | False |
| tab:MLC | 47c1418e0020 | midlat_cyclone | False | 10-07 06:00 | 10-02 00:00 | 10-07 06:00 | 2026100712 | 0 | 0 | 10/29 | False |
| tab:MLC | 47aeeb1f9b54 | midlat_cyclone | False | 10-20 06:00 | 10-21 00:00 | 10-21 00:00 | 2026102106 | 0 | 0 | 4/10 | False |
| tab:MLC | f3727a8efbc6 | midlat_cyclone | False | 10-22 00:00 | 10-22 00:00 | 10-22 00:00 | 2026102206 | 0 | 0 | 2/11 | False |
| tab:MLC | 9ad7027c17dc | midlat_cyclone | False | 10-24 12:00 | 10-25 00:00 | 10-25 00:00 | 2026102506 | 0 | 0 | 4/14 | False |
| tab:MLC | 08fac5554fb4 | midlat_cyclone | False | 10-25 00:00 | 10-26 00:00 | 10-26 00:00 | 2026102606 | 0 | 0 | 6/11 | False |
| tab:MLC | 52327cd61784 | midlat_cyclone | False | 10-26 12:00 | 10-26 00:00 | 10-26 12:00 | 2026102618 | 0 | 0 | 3/11 | False |
| tab:MLC | 450bed8d1975 | midlat_cyclone | False | 10-27 06:00 | 10-25 00:00 | 10-27 06:00 | 2026102712 | 0 | 0 | 7/25 | False |
| tab:MLC | 77a6e1faa78f | midlat_cyclone | False | 10-30 18:00 | 10-28 00:00 | 10-30 18:00 | 2026103100 | 0 | 0 | 2/19 | False |
| tab:MLC | bf564a80ac5f | midlat_cyclone | False | 10-31 00:00 | 10-30 00:00 | 10-31 00:00 | 2026103106 | 0 | 0 | 1/17 | False |

64 violating (context, event, frame) observations of 1364 total.

## Full table (global context; a=mid-life, b=just after cap, c=window end)

| event | type | ongoing | first | last fix | end | frame | marks | labels | canvas hits | field |
|---|---|---|---|---|---|---|---|---|---|---|
| 2f1a78bbfd6d | tropical_cyclone | False | 08-21 00:00 | 08-25 06:00 | 08-24 03:00 | a 2026082306 | 1 | 1 | 23/35 | False |
| 68ec790015c7 | tropical_cyclone | False | 08-21 00:00 | 08-25 18:00 | 08-25 21:00 | a 2026082312 | 1 | 1 | 27/39 | False |
| 524d59ac0fe7 | tropical_cyclone | False | 08-21 00:00 | 08-26 06:00 | 08-25 12:00 | a 2026082318 | 1 | 1 | 41/43 | False |
| 95180b67568a | tropical_cyclone | False | 08-21 12:00 | 08-27 00:00 | 08-27 00:00 | a 2026082406 | 1 | 1 | 30/45 | True |
| 9227f3e04f69 | tropical_cyclone | False | 08-21 00:00 | 08-28 00:00 | 08-28 03:00 | a 2026082412 | 1 | 1 | 33/57 | True |
| 013826c5379e | tropical_cyclone | False | 08-21 00:00 | 08-28 06:00 | 08-28 09:00 | a 2026082418 | 1 | 1 | 54/59 | True |
| e57f7ebfdf54 | midlat_cyclone | False | 08-23 12:00 | 08-25 12:00 | 08-26 00:00 | a 2026082418 | 1 | 1 | 7/9 | False |
| 2f1a78bbfd6d | tropical_cyclone | False | 08-21 00:00 | 08-25 06:00 | 08-24 03:00 | b 2026082512 | 0 | 0 | 0/35 | False |
| a0ce3336ca10 | midlat_cyclone | False | 08-23 18:00 | 08-26 18:00 | 08-27 00:00 | a 2026082512 | 1 | 1 | 12/13 | False |
| 68ec790015c7 | tropical_cyclone | False | 08-21 00:00 | 08-25 18:00 | 08-25 21:00 | b 2026082600 | 0 | 0 | 2/39 | False |
| e57f7ebfdf54 | midlat_cyclone | False | 08-23 12:00 | 08-25 12:00 | 08-26 00:00 | b 2026082606 | 0 | 0 | 1/9 | False |
| 524d59ac0fe7 | tropical_cyclone | False | 08-21 00:00 | 08-26 06:00 | 08-25 12:00 | b 2026082612 | 0 | 0 | 0/43 | False |
| 95180b67568a | tropical_cyclone | False | 08-21 12:00 | 08-27 00:00 | 08-27 00:00 | b 2026082706 | 0 | 0 | 3/45 | False |
| a0ce3336ca10 | midlat_cyclone | False | 08-23 18:00 | 08-26 18:00 | 08-27 00:00 | b 2026082706 | 0 | 0 | 0/13 | False |
| f2d932286433 | tropical_cyclone | False | 08-24 00:00 | 08-31 12:00 | 08-29 09:00 | a 2026082718 | 1 | 1 | 34/61 | False |
| 9227f3e04f69 | tropical_cyclone | False | 08-21 00:00 | 08-28 00:00 | 08-28 03:00 | b 2026082806 | 0 | 0 | 0/57 | False |
| 013826c5379e | tropical_cyclone | False | 08-21 00:00 | 08-28 06:00 | 08-28 09:00 | b 2026082812 | 0 | 0 | 0/59 | False |
| 168380aab44a | midlat_cyclone | False | 08-27 00:00 | 08-29 06:00 | 08-30 00:00 | a 2026082812 | 1 | 1 | 8/10 | False |
| 5a5bb77e19b3 | tropical_cyclone | False | 08-26 06:00 | 08-31 00:00 | 08-30 15:00 | a 2026082818 | 1 | 1 | 29/39 | True |
| 06be9280df4b | midlat_cyclone | False | 08-26 18:00 | 08-30 18:00 | 08-31 00:00 | a 2026082900 | 1 | 1 | 14/17 | False |
| 4dd3a55a14b9 | tropical_cyclone | False | 08-27 00:00 | 08-31 00:00 | 08-29 21:00 | a 2026082900 | 1 | 1 | 20/33 | True |
| 168380aab44a | midlat_cyclone | False | 08-27 00:00 | 08-29 06:00 | 08-30 00:00 | b 2026083006 | 0 | 0 | 0/10 | False |
| 5abc5a4b6214 | midlat_cyclone | False | 08-28 18:00 | 08-31 06:00 | 09-01 00:00 | a 2026083012 | 1 | 1 | 11/11 | False |
| 51a6978b7d36 | midlat_cyclone | False | 08-28 12:00 | 09-01 06:00 | 09-02 00:00 | a 2026083018 | 1 | 1 | 11/16 | False |
| 06be9280df4b | midlat_cyclone | False | 08-26 18:00 | 08-30 18:00 | 08-31 00:00 | b 2026083106 | 0 | 0 | 1/17 | False |
| 4dd3a55a14b9 | tropical_cyclone | False | 08-27 00:00 | 08-31 00:00 | 08-29 21:00 | b 2026083106 | 0 | 0 | 0/33 | False |
| 5a5bb77e19b3 | tropical_cyclone | False | 08-26 06:00 | 08-31 00:00 | 08-30 15:00 | b 2026083106 | 0 | 0 | 0/39 | False |
| 41ca1a5cf33c | tropical_cyclone | False | 08-28 18:00 | 09-03 06:00 | 09-02 06:00 | a 2026083112 | 1 | 1 | 31/45 | False |
| 8f747eb0052d | tropical_cyclone | False | 08-25 00:00 | 09-07 00:00 | 09-07 03:00 | a 2026083112 | 1 | 1 | 61/105 | True |
| 0dcf6b69bdc0 | midlat_cyclone | False | 08-29 06:00 | 09-03 00:00 | 09-03 00:00 | a 2026083118 | 1 | 1 | 16/20 | False |
| 49bd937a0cd9 | midlat_cyclone | False | 08-30 12:00 | 09-01 12:00 | 09-02 00:00 | a 2026083118 | 1 | 1 | 9/9 | False |
| f2d932286433 | tropical_cyclone | False | 08-24 00:00 | 08-31 12:00 | 08-29 09:00 | b 2026083118 | 0 | 0 | 0/61 | False |
| 5abc5a4b6214 | midlat_cyclone | False | 08-28 18:00 | 08-31 06:00 | 09-01 00:00 | b 2026090106 | 0 | 0 | 8/11 | False |
| 71cbd7eeaf25 | midlat_cyclone | False | 08-31 00:00 | 09-02 06:00 | 09-02 00:00 | a 2026090106 | 1 | 1 | 7/10 | False |
| 2d3a927184d6 | tropical_cyclone | False | 08-31 06:00 | 09-03 00:00 | 09-03 03:00 | a 2026090118 | 1 | 1 | 23/23 | False |
| 49bd937a0cd9 | midlat_cyclone | False | 08-30 12:00 | 09-01 12:00 | 09-02 00:00 | b 2026090206 | 0 | 0 | 0/9 | False |
| 51a6978b7d36 | midlat_cyclone | False | 08-28 12:00 | 09-01 06:00 | 09-02 00:00 | b 2026090206 | 0 | 0 | 1/16 | False |
| 71cbd7eeaf25 | midlat_cyclone | False | 08-31 00:00 | 09-02 06:00 | 09-02 00:00 | b 2026090212 | 0 | 0 | 3/10 | False |
| fa72d5dcb4e0 | midlat_cyclone | False | 08-31 00:00 | 09-04 00:00 | 09-05 00:00 | a 2026090212 | 1 | 1 | 11/17 | False |
| 37bdab61dce6 | tropical_cyclone | False | 08-25 18:00 | 09-11 00:00 | 09-11 03:00 | a 2026090300 | 1 | 1 | 74/131 | True |
| 0dcf6b69bdc0 | midlat_cyclone | False | 08-29 06:00 | 09-03 00:00 | 09-03 00:00 | b 2026090306 | 0 | 0 | 0/20 | False |
| 2d3a927184d6 | tropical_cyclone | False | 08-31 06:00 | 09-03 00:00 | 09-03 03:00 | b 2026090306 | 0 | 0 | 0/23 | False |
| 41ca1a5cf33c | tropical_cyclone | False | 08-28 18:00 | 09-03 06:00 | 09-02 06:00 | b 2026090312 | 0 | 0 | 0/45 | False |
| 92b1a98ceba6 | midlat_cyclone | False | 09-01 18:00 | 09-04 12:00 | 09-05 00:00 | a 2026090312 | 1 | 1 | 9/12 | False |
| 2106cb080da3 | midlat_cyclone | False | 09-01 00:00 | 09-06 06:00 | 09-04 00:00 | a 2026090318 | 1 | 1 | 13/22 | False |
| a966ddfde13c | tropical_cyclone | False | 08-29 18:00 | 09-08 12:00 | 09-08 15:00 | a 2026090318 | 1 | 1 | 53/79 | True |
| aa15e18db971 | midlat_cyclone | False | 09-02 18:00 | 09-05 06:00 | 09-05 00:00 | a 2026090400 | 1 | 1 | 6/11 | False |
| 92b1a98ceba6 | midlat_cyclone | False | 09-01 18:00 | 09-04 12:00 | 09-05 00:00 | b 2026090506 | 0 | 0 | 0/12 | False |
| fa72d5dcb4e0 | midlat_cyclone | False | 08-31 00:00 | 09-04 00:00 | 09-05 00:00 | b 2026090506 | 0 | 0 | 0/17 | False |
| aa15e18db971 | midlat_cyclone | False | 09-02 18:00 | 09-05 06:00 | 09-05 00:00 | b 2026090512 | 0 | 0 | 0/11 | False |
| d0641847f100 | tropical_cyclone | False | 09-01 06:00 | 09-09 12:00 | 09-05 09:00 | a 2026090512 | 1 | 1 | 50/67 | False |
| c0f5a145a343 | midlat_cyclone | False | 09-04 06:00 | 09-08 00:00 | 09-08 00:00 | a 2026090606 | 1 | 1 | 9/16 | False |
| 2106cb080da3 | midlat_cyclone | False | 09-01 00:00 | 09-06 06:00 | 09-04 00:00 | b 2026090612 | 0 | 0 | 0/22 | False |
| 3684807ce119 | midlat_cyclone | False | 09-05 06:00 | 09-08 06:00 | 09-09 00:00 | a 2026090706 | 1 | 1 | 9/13 | False |
| 8f747eb0052d | tropical_cyclone | False | 08-25 00:00 | 09-07 00:00 | 09-07 03:00 | b 2026090706 | 0 | 0 | 1/105 | False |
| c0f5a145a343 | midlat_cyclone | False | 09-04 06:00 | 09-08 00:00 | 09-08 00:00 | b 2026090806 | 0 | 0 | 0/16 | False |
| a966ddfde13c | tropical_cyclone | False | 08-29 18:00 | 09-08 12:00 | 09-08 15:00 | b 2026090818 | 0 | 0 | 14/79 | False |
| bd06f162e627 | midlat_cyclone | False | 09-07 00:00 | 09-10 06:00 | 09-11 00:00 | a 2026090900 | 1 | 1 | 10/14 | False |
| 3684807ce119 | midlat_cyclone | False | 09-05 06:00 | 09-08 06:00 | 09-09 00:00 | b 2026090906 | 0 | 0 | 2/13 | False |
| 32230442769b | midlat_cyclone | False | 09-08 00:00 | 09-11 00:00 | 09-10 00:00 | a 2026090912 | 1 | 1 | 8/13 | False |
| 98615c5da0fa | midlat_cyclone | False | 09-07 06:00 | 09-11 12:00 | 09-10 00:00 | a 2026090912 | 1 | 1 | 15/18 | False |
| d0641847f100 | tropical_cyclone | False | 09-01 06:00 | 09-09 12:00 | 09-05 09:00 | b 2026090918 | 0 | 0 | 0/67 | False |
| 32230442769b | midlat_cyclone | False | 09-08 00:00 | 09-11 00:00 | 09-10 00:00 | b 2026091106 | 0 | 0 | 0/13 | False |
| 37bdab61dce6 | tropical_cyclone | False | 08-25 18:00 | 09-11 00:00 | 09-11 03:00 | b 2026091106 | 0 | 0 | 0/131 | False |
| bd06f162e627 | midlat_cyclone | False | 09-07 00:00 | 09-10 06:00 | 09-11 00:00 | b 2026091106 | 0 | 0 | 0/14 | False |
| 3006ff3558ba | tropical_cyclone | False | 09-07 18:00 | 09-15 12:00 | 09-14 21:00 | a 2026091118 | 1 | 1 | 40/63 | False |
| 98615c5da0fa | midlat_cyclone | False | 09-07 06:00 | 09-11 12:00 | 09-10 00:00 | b 2026091118 | 0 | 0 | 0/18 | False |
| f3cd2c1042fc | midlat_cyclone | False | 09-11 00:00 | 09-14 12:00 | 09-14 00:00 | a 2026091218 | 1 | 1 | 9/15 | False |
| 21f32fd04468 | midlat_cyclone | False | 09-11 06:00 | 09-15 12:00 | 09-16 00:00 | a 2026091318 | 1 | 1 | 14/18 | False |
| 452175874da2 | midlat_cyclone | False | 09-12 12:00 | 09-16 18:00 | 09-16 00:00 | a 2026091418 | 1 | 1 | 10/18 | False |
| f3cd2c1042fc | midlat_cyclone | False | 09-11 00:00 | 09-14 12:00 | 09-14 00:00 | b 2026091418 | 0 | 0 | 0/15 | False |
| de869c0ce1f4 | midlat_cyclone | False | 09-11 06:00 | 09-19 06:00 | 09-14 00:00 | a 2026091506 | 1 | 1 | 19/33 | False |
| 3006ff3558ba | tropical_cyclone | False | 09-07 18:00 | 09-15 12:00 | 09-14 21:00 | b 2026091518 | 0 | 0 | 28/63 | False |
| 53edb6c0a38a | midlat_cyclone | False | 09-14 18:00 | 09-16 18:00 | 09-17 00:00 | a 2026091600 | 1 | 1 | 6/9 | False |
| 21f32fd04468 | midlat_cyclone | False | 09-11 06:00 | 09-15 12:00 | 09-16 00:00 | b 2026091606 | 0 | 0 | 2/18 | False |
| 452175874da2 | midlat_cyclone | False | 09-12 12:00 | 09-16 18:00 | 09-16 00:00 | b 2026091700 | 0 | 0 | 0/18 | False |
| 53edb6c0a38a | midlat_cyclone | False | 09-14 18:00 | 09-16 18:00 | 09-17 00:00 | b 2026091706 | 0 | 0 | 0/9 | False |
| 4fc0d133541c | midlat_cyclone | False | 09-17 00:00 | 09-19 12:00 | 09-19 00:00 | a 2026091806 | 1 | 1 | 11/11 | False |
| 9b29aec3769f | tropical_cyclone | False | 09-15 12:00 | 09-22 12:00 | 09-22 15:00 | a 2026091900 | 1 | 1 | 41/57 | True |
| d30504cba48e | midlat_cyclone | False | 09-17 18:00 | 09-20 06:00 | 09-21 00:00 | a 2026091912 | 1 | 1 | 9/11 | False |
| de869c0ce1f4 | midlat_cyclone | False | 09-11 06:00 | 09-19 06:00 | 09-14 00:00 | b 2026091912 | 0 | 0 | 3/33 | False |
| 4fc0d133541c | midlat_cyclone | False | 09-17 00:00 | 09-19 12:00 | 09-19 00:00 | b 2026091918 | 0 | 0 | 0/11 | False |
| 6a435f91d896 | midlat_cyclone | False | 09-18 12:00 | 09-21 06:00 | 09-22 00:00 | a 2026092006 | 1 | 1 | 8/12 | False |
| d30504cba48e | midlat_cyclone | False | 09-17 18:00 | 09-20 06:00 | 09-21 00:00 | b 2026092106 | 0 | 0 | 0/11 | False |
| de9fc11c9cef | midlat_cyclone | False | 09-19 06:00 | 09-22 18:00 | 09-23 00:00 | a 2026092106 | 1 | 1 | 11/15 | False |
| 6a435f91d896 | midlat_cyclone | False | 09-18 12:00 | 09-21 06:00 | 09-22 00:00 | b 2026092206 | 0 | 0 | 0/12 | False |
| be183ee601c7 | tropical_cyclone | False | 09-16 18:00 | 09-27 18:00 | 09-27 21:00 | a 2026092206 | 1 | 1 | 73/89 | True |
| 9b29aec3769f | tropical_cyclone | False | 09-15 12:00 | 09-22 12:00 | 09-22 15:00 | b 2026092218 | 0 | 0 | 0/57 | False |
| de9fc11c9cef | midlat_cyclone | False | 09-19 06:00 | 09-22 18:00 | 09-23 00:00 | b 2026092306 | 0 | 0 | 0/15 | False |
| 786a036ca8d0 | tropical_cyclone | False | 09-22 18:00 | 09-24 00:00 | 09-24 03:00 | a 2026092312 | 1 | 1 | 11/11 | False |
| cf0d74b427ab | midlat_cyclone | False | 09-22 12:00 | 09-24 18:00 | 09-24 00:00 | a 2026092318 | 1 | 1 | 7/10 | False |
| dd9825d6b56e | tropical_cyclone | False | 09-17 12:00 | 09-30 00:00 | 09-30 00:00 | a 2026092318 | 1 | 1 | 61/101 | True |
| f0e20c5dccfc | tropical_cyclone | False | 09-17 12:00 | 09-29 18:00 | 09-29 09:00 | a 2026092318 | 1 | 1 | 66/99 | False |
| 0b91bcef5da1 | midlat_cyclone | False | 09-22 00:00 | 09-26 06:00 | 09-26 00:00 | a 2026092406 | 1 | 1 | 11/18 | False |
| 786a036ca8d0 | tropical_cyclone | False | 09-22 18:00 | 09-24 00:00 | 09-24 03:00 | b 2026092406 | 0 | 0 | 0/11 | False |
| ed1b9012b680 | tropical_cyclone | False | 09-22 18:00 | 09-26 18:00 | 09-26 21:00 | a 2026092418 | 1 | 1 | 21/33 | False |
| cf0d74b427ab | midlat_cyclone | False | 09-22 12:00 | 09-24 18:00 | 09-24 00:00 | b 2026092500 | 0 | 0 | 0/10 | False |
| 8fce6c60fd09 | midlat_cyclone | False | 09-23 18:00 | 09-27 06:00 | 09-27 00:00 | a 2026092512 | 1 | 1 | 11/15 | False |
| 1a992ebc5446 | tropical_cyclone | False | 09-10 12:00 | 10-11 12:00 | 10-10 15:00 | a 2026092600 | 1 | 1 | 139/249 | True |
| 0b91bcef5da1 | midlat_cyclone | False | 09-22 00:00 | 09-26 06:00 | 09-26 00:00 | b 2026092612 | 0 | 0 | 0/18 | False |
| ed1b9012b680 | tropical_cyclone | False | 09-22 18:00 | 09-26 18:00 | 09-26 21:00 | b 2026092700 | 0 | 0 | 0/33 | False |
| 4b560d4b31f5 | tropical_cyclone | False | 09-23 06:00 | 10-01 06:00 | 09-30 15:00 | a 2026092706 | 1 | 1 | 42/65 | True |
| 8fce6c60fd09 | midlat_cyclone | False | 09-23 18:00 | 09-27 06:00 | 09-27 00:00 | b 2026092712 | 0 | 0 | 0/15 | False |
| be183ee601c7 | tropical_cyclone | False | 09-16 18:00 | 09-27 18:00 | 09-27 21:00 | b 2026092800 | 0 | 0 | 45/89 | False |
| 2283fe825f9b | midlat_cyclone | False | 09-27 12:00 | 09-30 00:00 | 09-30 00:00 | a 2026092818 | 1 | 1 | 11/11 | False |
| 3b0231513926 | tropical_cyclone | False | 09-26 18:00 | 09-30 12:00 | 09-30 15:00 | a 2026092818 | 1 | 1 | 22/31 | False |
| f0e20c5dccfc | tropical_cyclone | False | 09-17 12:00 | 09-29 18:00 | 09-29 09:00 | b 2026093000 | 0 | 0 | 0/99 | False |
| 2283fe825f9b | midlat_cyclone | False | 09-27 12:00 | 09-30 00:00 | 09-30 00:00 | b 2026093006 | 0 | 0 | 0/11 | False |
| dd9825d6b56e | tropical_cyclone | False | 09-17 12:00 | 09-30 00:00 | 09-30 00:00 | b 2026093006 | 0 | 0 | 47/101 | False |
| 3b0231513926 | tropical_cyclone | False | 09-26 18:00 | 09-30 12:00 | 09-30 15:00 | b 2026093018 | 0 | 0 | 0/31 | False |
| 4b560d4b31f5 | tropical_cyclone | False | 09-23 06:00 | 10-01 06:00 | 09-30 15:00 | b 2026100112 | 0 | 0 | 0/65 | False |
| fcd3ce47c725 | midlat_cyclone | False | 09-30 06:00 | 10-04 06:00 | 10-03 00:00 | a 2026100206 | 1 | 1 | 11/17 | False |
| 720d16977a23 | midlat_cyclone | False | 09-30 18:00 | 10-04 00:00 | 10-05 00:00 | a 2026100300 | 1 | 1 | 12/14 | False |
| 097d779c88fd | tropical_cyclone | False | 09-25 00:00 | 10-11 12:00 | 10-11 12:00 | a 2026100306 | 1 | 1 | 84/133 | True |
| 47c1418e0020 | midlat_cyclone | False | 09-30 06:00 | 10-07 06:00 | 10-02 00:00 | a 2026100318 | 1 | 1 | 21/29 | False |
| 8f8538d47139 | tropical_cyclone | False | 09-30 00:00 | 10-07 06:00 | 10-07 09:00 | a 2026100318 | 1 | 1 | 43/59 | True |
| eb5968513e1c | midlat_cyclone | False | 09-28 12:00 | 10-10 12:00 | 10-02 00:00 | a 2026100412 | 1 | 1 | 25/49 | False |
| fcd3ce47c725 | midlat_cyclone | False | 09-30 06:00 | 10-04 06:00 | 10-03 00:00 | b 2026100412 | 0 | 0 | 0/17 | False |
| 720d16977a23 | midlat_cyclone | False | 09-30 18:00 | 10-04 00:00 | 10-05 00:00 | b 2026100506 | 0 | 0 | 8/14 | False |
| c0779432587c | midlat_cyclone | False | 10-03 00:00 | 10-07 18:00 | 10-07 00:00 | a 2026100512 | 1 | 1 | 16/20 | False |
| e7396df75419 | midlat_cyclone | False | 10-03 12:00 | 10-07 18:00 | 10-06 00:00 | a 2026100518 | 1 | 1 | 18/18 | False |
| 47c1418e0020 | midlat_cyclone | False | 09-30 06:00 | 10-07 06:00 | 10-02 00:00 | b 2026100712 | 0 | 0 | 10/29 | False |
| 8f8538d47139 | tropical_cyclone | False | 09-30 00:00 | 10-07 06:00 | 10-07 09:00 | b 2026100712 | 0 | 0 | 0/59 | False |
| c0779432587c | midlat_cyclone | False | 10-03 00:00 | 10-07 18:00 | 10-07 00:00 | b 2026100800 | 0 | 0 | 0/20 | False |
| e7396df75419 | midlat_cyclone | False | 10-03 12:00 | 10-07 18:00 | 10-06 00:00 | b 2026100800 | 0 | 0 | 0/18 | False |
| 27222b8db889 | tropical_cyclone | False | 10-04 18:00 | 10-11 12:00 | 10-10 12:00 | a 2026100806 | 1 | 1 | 35/55 | True |
| e4d8a743f5a0 | tropical_cyclone | False | 10-05 00:00 | 10-13 18:00 | 10-13 21:00 | a 2026100912 | 1 | 1 | 50/71 | True |
| 5e360d5663b4 | tropical_cyclone | False | 10-04 12:00 | 10-16 18:00 | 10-16 15:00 | a 2026101018 | 1 | 1 | 98/99 | True |
| eb5968513e1c | midlat_cyclone | False | 09-28 12:00 | 10-10 12:00 | 10-02 00:00 | b 2026101018 | 0 | 0 | 0/49 | False |
| 097d779c88fd | tropical_cyclone | False | 09-25 00:00 | 10-11 12:00 | 10-11 12:00 | b 2026101118 | 0 | 0 | 49/133 | False |
| 1a992ebc5446 | tropical_cyclone | False | 09-10 12:00 | 10-11 12:00 | 10-10 15:00 | b 2026101118 | 0 | 0 | 9/249 | False |
| 27222b8db889 | tropical_cyclone | False | 10-04 18:00 | 10-11 12:00 | 10-10 12:00 | b 2026101118 | 0 | 0 | 0/55 | False |
| 8fd7374369ae | midlat_cyclone | False | 10-09 12:00 | 10-14 00:00 | 10-14 00:00 | a 2026101118 | 1 | 1 | 19/19 | False |
| 5ae8b7da2120 | midlat_cyclone | False | 10-11 00:00 | 10-13 12:00 | 10-14 00:00 | a 2026101212 | 1 | 1 | 9/11 | False |
| b4f987194bfa | midlat_cyclone | False | 10-10 18:00 | 10-13 06:00 | 10-14 00:00 | a 2026101212 | 1 | 1 | 10/11 | False |
| 7862dd70553e | midlat_cyclone | False | 10-11 06:00 | 10-14 00:00 | 10-15 00:00 | a 2026101306 | 1 | 1 | 10/12 | False |
| 9102f927da6b | midlat_cyclone | False | 10-11 12:00 | 10-16 00:00 | 10-16 00:00 | a 2026101318 | 1 | 0 | 11/19 | False |
| e4d8a743f5a0 | tropical_cyclone | False | 10-05 00:00 | 10-13 18:00 | 10-13 21:00 | b 2026101400 | 0 | 0 | 0/71 | False |
| 5ae8b7da2120 | midlat_cyclone | False | 10-11 00:00 | 10-13 12:00 | 10-14 00:00 | b 2026101406 | 0 | 0 | 0/11 | False |
| 8fd7374369ae | midlat_cyclone | False | 10-09 12:00 | 10-14 00:00 | 10-14 00:00 | b 2026101406 | 0 | 0 | 0/19 | False |
| b4f987194bfa | midlat_cyclone | False | 10-10 18:00 | 10-13 06:00 | 10-14 00:00 | b 2026101406 | 0 | 0 | 0/11 | False |
| b6b2e09f3b99 | midlat_cyclone | False | 10-11 18:00 | 10-16 18:00 | 10-16 00:00 | a 2026101406 | 1 | 1 | 18/21 | False |
| a6a3706f59d4 | midlat_cyclone | False | 10-13 00:00 | 10-17 00:00 | 10-17 00:00 | a 2026101500 | 1 | 1 | 13/17 | False |
| 7862dd70553e | midlat_cyclone | False | 10-11 06:00 | 10-14 00:00 | 10-15 00:00 | b 2026101506 | 0 | 0 | 0/12 | False |
| 3d3fbf19f543 | midlat_cyclone | False | 10-12 00:00 | 10-18 18:00 | 10-19 00:00 | a 2026101512 | 1 | 1 | 15/28 | False |
| a564bf02954f | tropical_cyclone | False | 10-11 06:00 | 10-20 00:00 | 10-20 00:00 | a 2026101518 | 1 | 1 | 71/71 | True |
| 00dc299bfcbd | midlat_cyclone | False | 10-14 06:00 | 10-17 18:00 | 10-16 00:00 | a 2026101600 | 1 | 1 | 8/15 | False |
| 9102f927da6b | midlat_cyclone | False | 10-11 12:00 | 10-16 00:00 | 10-16 00:00 | b 2026101606 | 0 | 0 | 0/19 | False |
| c066286e3678 | midlat_cyclone | False | 10-14 00:00 | 10-18 12:00 | 10-16 00:00 | a 2026101606 | 1 | 1 | 10/19 | False |
| 5e360d5663b4 | tropical_cyclone | False | 10-04 12:00 | 10-16 18:00 | 10-16 15:00 | b 2026101700 | 0 | 0 | 39/99 | False |
| b6b2e09f3b99 | midlat_cyclone | False | 10-11 18:00 | 10-16 18:00 | 10-16 00:00 | b 2026101700 | 0 | 0 | 0/21 | False |
| a6a3706f59d4 | midlat_cyclone | False | 10-13 00:00 | 10-17 00:00 | 10-17 00:00 | b 2026101706 | 0 | 0 | 0/17 | False |
| 7171619838ce | tropical_cyclone | False | 10-16 06:00 | 10-19 06:00 | 10-19 09:00 | a 2026101718 | 1 | 1 | 18/25 | True |
| 00dc299bfcbd | midlat_cyclone | False | 10-14 06:00 | 10-17 18:00 | 10-16 00:00 | b 2026101800 | 0 | 0 | 0/15 | False |
| c066286e3678 | midlat_cyclone | False | 10-14 00:00 | 10-18 12:00 | 10-16 00:00 | b 2026101818 | 0 | 0 | 0/19 | False |
| 7cec41fec1ef | midlat_cyclone | False | 10-16 18:00 | 10-20 06:00 | 10-21 00:00 | a 2026101900 | 1 | 1 | 10/15 | False |
| 3d3fbf19f543 | midlat_cyclone | False | 10-12 00:00 | 10-18 18:00 | 10-19 00:00 | b 2026101906 | 0 | 0 | 0/28 | False |
| 47aeeb1f9b54 | midlat_cyclone | False | 10-18 00:00 | 10-20 06:00 | 10-21 00:00 | a 2026101912 | 1 | 1 | 7/10 | False |
| 7171619838ce | tropical_cyclone | False | 10-16 06:00 | 10-19 06:00 | 10-19 09:00 | b 2026101912 | 0 | 0 | 0/25 | False |
| 465d6123facd | midlat_cyclone | False | 10-17 00:00 | 10-23 12:00 | 10-20 00:00 | a 2026102006 | 1 | 1 | 19/27 | False |
| a564bf02954f | tropical_cyclone | False | 10-11 06:00 | 10-20 00:00 | 10-20 00:00 | b 2026102006 | 0 | 0 | 8/71 | False |
| afdd2de7aabd | tropical_cyclone | False | 10-14 12:00 | 10-26 00:00 | 10-26 03:00 | a 2026102006 | 1 | 1 | 52/93 | True |
| 09a98560b2ac | tropical_cyclone | False | 10-18 18:00 | 10-22 18:00 | 10-22 21:00 | a 2026102018 | 1 | 1 | 28/33 | False |
| 0c221ac1eb33 | midlat_cyclone | False | 10-18 12:00 | 10-22 18:00 | 10-21 00:00 | a 2026102018 | 1 | 1 | 12/18 | False |
| f3727a8efbc6 | midlat_cyclone | False | 10-19 12:00 | 10-22 00:00 | 10-22 00:00 | a 2026102018 | 1 | 1 | 8/11 | False |
| 47aeeb1f9b54 | midlat_cyclone | False | 10-18 00:00 | 10-20 06:00 | 10-21 00:00 | b 2026102106 | 0 | 0 | 4/10 | False |
| 7cec41fec1ef | midlat_cyclone | False | 10-16 18:00 | 10-20 06:00 | 10-21 00:00 | b 2026102106 | 0 | 0 | 0/15 | False |
| 4783c1aa0dce | tropical_cyclone | False | 10-20 06:00 | 10-24 00:00 | 10-23 15:00 | a 2026102206 | 1 | 1 | 30/31 | False |
| f3727a8efbc6 | midlat_cyclone | False | 10-19 12:00 | 10-22 00:00 | 10-22 00:00 | b 2026102206 | 0 | 0 | 2/11 | False |
| f37b74827ecb | midlat_cyclone | False | 10-20 06:00 | 10-24 18:00 | 10-25 00:00 | a 2026102218 | 1 | 1 | 11/19 | False |
| 09a98560b2ac | tropical_cyclone | False | 10-18 18:00 | 10-22 18:00 | 10-22 21:00 | b 2026102300 | 0 | 0 | 0/33 | False |
| 0c221ac1eb33 | midlat_cyclone | False | 10-18 12:00 | 10-22 18:00 | 10-21 00:00 | b 2026102300 | 0 | 0 | 0/18 | False |
| 9ad7027c17dc | midlat_cyclone | False | 10-21 06:00 | 10-24 12:00 | 10-25 00:00 | a 2026102306 | 1 | 1 | 10/14 | False |
| 465d6123facd | midlat_cyclone | False | 10-17 00:00 | 10-23 12:00 | 10-20 00:00 | b 2026102318 | 0 | 0 | 0/27 | False |
| 08fac5554fb4 | midlat_cyclone | False | 10-22 12:00 | 10-25 00:00 | 10-26 00:00 | a 2026102406 | 1 | 1 | 10/11 | False |
| 450bed8d1975 | midlat_cyclone | False | 10-21 06:00 | 10-27 06:00 | 10-25 00:00 | a 2026102406 | 1 | 1 | 14/25 | False |
| 4783c1aa0dce | tropical_cyclone | False | 10-20 06:00 | 10-24 00:00 | 10-23 15:00 | b 2026102406 | 0 | 0 | 0/31 | False |
| c6c77cff2115 | tropical_cyclone | False | 10-18 18:00 | 10-30 00:00 | 10-29 15:00 | a 2026102412 | 1 | 1 | 61/91 | True |
| bfe1bfdb56a1 | midlat_cyclone | False | 10-21 18:00 | 10-27 00:00 | 10-28 00:00 | a 2026102500 | 1 | 1 | 15/22 | False |
| 52327cd61784 | midlat_cyclone | False | 10-24 00:00 | 10-26 12:00 | 10-26 00:00 | a 2026102506 | 1 | 1 | 8/11 | False |
| 9ad7027c17dc | midlat_cyclone | False | 10-21 06:00 | 10-24 12:00 | 10-25 00:00 | b 2026102506 | 0 | 0 | 4/14 | False |
| f37b74827ecb | midlat_cyclone | False | 10-20 06:00 | 10-24 18:00 | 10-25 00:00 | b 2026102506 | 0 | 0 | 0/19 | False |
| 5e5ce42a239e | tropical_cyclone | False | 10-24 06:00 | 10-27 00:00 | 10-26 09:00 | a 2026102518 | 1 | 1 | 23/23 | False |
| faa3a35f6047 | midlat_cyclone | False | 10-24 12:00 | 10-27 00:00 | 10-27 00:00 | a 2026102518 | 1 | 1 | 7/11 | False |
| 08fac5554fb4 | midlat_cyclone | False | 10-22 12:00 | 10-25 00:00 | 10-26 00:00 | b 2026102606 | 0 | 0 | 6/11 | False |
| afdd2de7aabd | tropical_cyclone | False | 10-14 12:00 | 10-26 00:00 | 10-26 03:00 | b 2026102606 | 0 | 0 | 0/93 | False |
| 1f492d08bdce | midlat_cyclone | False | 10-24 00:00 | 10-29 06:00 | 10-29 00:00 | a 2026102618 | 1 | 1 | 12/22 | False |
| 52327cd61784 | midlat_cyclone | False | 10-24 00:00 | 10-26 12:00 | 10-26 00:00 | b 2026102618 | 0 | 0 | 3/11 | False |
| 5e5ce42a239e | tropical_cyclone | False | 10-24 06:00 | 10-27 00:00 | 10-26 09:00 | b 2026102706 | 0 | 0 | 0/23 | False |
| faa3a35f6047 | midlat_cyclone | False | 10-24 12:00 | 10-27 00:00 | 10-27 00:00 | b 2026102706 | 0 | 0 | 0/11 | False |
| 450bed8d1975 | midlat_cyclone | False | 10-21 06:00 | 10-27 06:00 | 10-25 00:00 | b 2026102712 | 0 | 0 | 7/25 | False |
| f460b6e46636 | midlat_cyclone | False | 10-25 12:00 | 10-29 12:00 | 10-29 00:00 | a 2026102712 | 1 | 1 | 12/17 | False |
| 77a34212f688 | midlat_cyclone | False | 10-25 06:00 | 10-29 00:00 | 10-30 00:00 | a 2026102718 | 1 | 1 | 11/16 | False |
| c29831141981 | tropical_cyclone | False | 10-26 00:00 | 10-29 06:00 | 10-28 15:00 | a 2026102718 | 1 | 1 | 25/27 | True |
| bfe1bfdb56a1 | midlat_cyclone | False | 10-21 18:00 | 10-27 00:00 | 10-28 00:00 | b 2026102806 | 0 | 0 | 0/22 | False |
| 77a6e1faa78f | midlat_cyclone | False | 10-26 06:00 | 10-30 18:00 | 10-28 00:00 | a 2026102812 | 1 | 1 | 12/19 | False |
| bf564a80ac5f | midlat_cyclone | False | 10-27 00:00 | 10-31 00:00 | 10-30 00:00 | a 2026102900 | 1 | 1 | 9/17 | False |
| 0978cee0a3c4 | midlat_cyclone | False | 10-27 12:00 | 10-31 00:00 | 10-31 00:00 | a 2026102906 | 1 | 1 | 8/15 | False |
| 3b4108d8a8d0 | midlat_cyclone | False | 10-27 12:00 | 10-31 00:00 | 10-30 00:00 | a 2026102906 | 1 | 1 | 9/15 | False |
| 1614fd3a0005 | midlat_cyclone | False | 10-27 00:00 | 10-31 12:00 | 11-01 00:00 | a 2026102912 | 1 | 1 | 11/19 | False |
| 1f492d08bdce | midlat_cyclone | False | 10-24 00:00 | 10-29 06:00 | 10-29 00:00 | b 2026102912 | 0 | 0 | 0/22 | False |
| c29831141981 | tropical_cyclone | False | 10-26 00:00 | 10-29 06:00 | 10-28 15:00 | b 2026102912 | 0 | 0 | 0/27 | False |
| f460b6e46636 | midlat_cyclone | False | 10-25 12:00 | 10-29 12:00 | 10-29 00:00 | b 2026102918 | 0 | 0 | 0/17 | False |
| 1324816002ea | midlat_cyclone | False | 10-28 12:00 | 10-31 12:00 | 10-30 00:00 | a 2026103000 | 1 | 1 | 11/13 | False |
| 2fa8f8b34bec | tropical_cyclone | False | 10-23 12:00 | 11-05 06:00 | 11-05 09:00 | a 2026103000 | 1 | 1 | 56/103 | True |
| 77a34212f688 | midlat_cyclone | False | 10-25 06:00 | 10-29 00:00 | 10-30 00:00 | b 2026103006 | 0 | 0 | 0/16 | False |
| c6c77cff2115 | tropical_cyclone | False | 10-18 18:00 | 10-30 00:00 | 10-29 15:00 | b 2026103006 | 0 | 0 | 0/91 | False |
| e2ef32e60ed5 | midlat_cyclone | False | 10-28 12:00 | 10-31 06:00 | 11-01 00:00 | a 2026103006 | 1 | 1 | 8/12 | False |
| 77a6e1faa78f | midlat_cyclone | False | 10-26 06:00 | 10-30 18:00 | 10-28 00:00 | b 2026103100 | 0 | 0 | 2/19 | False |
| 0978cee0a3c4 | midlat_cyclone | False | 10-27 12:00 | 10-31 00:00 | 10-31 00:00 | b 2026103106 | 0 | 0 | 0/15 | False |
| 3b4108d8a8d0 | midlat_cyclone | False | 10-27 12:00 | 10-31 00:00 | 10-30 00:00 | b 2026103106 | 0 | 0 | 0/15 | False |
| bf564a80ac5f | midlat_cyclone | False | 10-27 00:00 | 10-31 00:00 | 10-30 00:00 | b 2026103106 | 0 | 0 | 1/17 | False |
| 1324816002ea | midlat_cyclone | False | 10-28 12:00 | 10-31 12:00 | 10-30 00:00 | b 2026103118 | 0 | 0 | 0/13 | False |
| 1614fd3a0005 | midlat_cyclone | False | 10-27 00:00 | 10-31 12:00 | 11-01 00:00 | b 2026110106 | 0 | 0 | 0/19 | False |
| e2ef32e60ed5 | midlat_cyclone | False | 10-28 12:00 | 10-31 06:00 | 11-01 00:00 | b 2026110106 | 0 | 0 | 0/12 | False |
| 72284f83d056 | midlat_cyclone | False | 10-30 12:00 | 11-03 12:00 | 11-04 00:00 | a 2026110118 | 1 | 1 | 17/17 | False |
| 83ebcc85928e | midlat_cyclone | False | 10-31 06:00 | 11-03 18:00 | 11-03 00:00 | a 2026110200 | 1 | 1 | 13/15 | False |
| b32081a3b590 | midlat_cyclone | False | 10-31 06:00 | 11-03 12:00 | 11-03 00:00 | a 2026110200 | 1 | 1 | 11/14 | False |
| 874983a234bc | midlat_cyclone | False | 10-31 18:00 | 11-03 18:00 | 11-03 00:00 | a 2026110206 | 1 | 1 | 7/13 | False |
| de83ed02806d | tropical_cyclone | False | 11-01 00:00 | 11-03 06:00 | 11-03 09:00 | a 2026110206 | 1 | 1 | 19/19 | False |
| 3f37cf13808f | midlat_cyclone | False | 10-31 18:00 | 11-03 18:00 | 11-04 00:00 | a 2026110212 | 1 | 1 | 12/13 | False |
| 41f15548d202 | midlat_cyclone | False | 11-01 12:00 | 11-03 18:00 | 11-04 00:00 | a 2026110218 | 1 | 1 | 6/10 | False |
| de83ed02806d | tropical_cyclone | False | 11-01 00:00 | 11-03 06:00 | 11-03 09:00 | b 2026110312 | 0 | 0 | 0/19 | False |
| b32081a3b590 | midlat_cyclone | False | 10-31 06:00 | 11-03 12:00 | 11-03 00:00 | b 2026110318 | 0 | 0 | 0/14 | False |
| 83ebcc85928e | midlat_cyclone | False | 10-31 06:00 | 11-03 18:00 | 11-03 00:00 | b 2026110400 | 0 | 0 | 0/15 | False |
| 874983a234bc | midlat_cyclone | False | 10-31 18:00 | 11-03 18:00 | 11-03 00:00 | b 2026110400 | 0 | 0 | 0/13 | False |
| 3f37cf13808f | midlat_cyclone | False | 10-31 18:00 | 11-03 18:00 | 11-04 00:00 | b 2026110406 | 0 | 0 | 0/13 | False |
| 41f15548d202 | midlat_cyclone | False | 11-01 12:00 | 11-03 18:00 | 11-04 00:00 | b 2026110406 | 0 | 0 | 0/10 | False |
| 72284f83d056 | midlat_cyclone | False | 10-30 12:00 | 11-03 12:00 | 11-04 00:00 | b 2026110406 | 0 | 0 | 0/17 | False |
| 2fa8f8b34bec | tropical_cyclone | False | 10-23 12:00 | 11-05 06:00 | 11-05 09:00 | b 2026110512 | 0 | 0 | 0/103 | False |
| c4b0d2570c94 | midlat_cyclone | False | 11-05 00:00 | 11-07 06:00 | 11-08 00:00 | a 2026110612 | 1 | 1 | 7/10 | False |
| cca0c2eb21f6 | midlat_cyclone | False | 11-05 00:00 | 11-08 18:00 | 11-08 00:00 | a 2026110700 | 1 | 1 | 12/16 | False |
| dcdf246a84eb | tropical_cyclone | False | 11-02 12:00 | 11-12 12:00 | 11-12 06:00 | a 2026110712 | 1 | 1 | 50/81 | False |
| c4b0d2570c94 | midlat_cyclone | False | 11-05 00:00 | 11-07 06:00 | 11-08 00:00 | b 2026110806 | 0 | 0 | 0/10 | False |
| 9518a6c054ff | tropical_cyclone | False | 11-06 06:00 | 11-11 12:00 | 11-11 15:00 | a 2026110900 | 1 | 1 | 32/43 | False |
| cca0c2eb21f6 | midlat_cyclone | False | 11-05 00:00 | 11-08 18:00 | 11-08 00:00 | b 2026110900 | 0 | 0 | 0/16 | False |
| d209cb72530b | tropical_cyclone | False | 11-07 18:00 | 11-10 00:00 | 11-08 09:00 | a 2026110900 | 1 | 1 | 18/19 | False |
| d209cb72530b | tropical_cyclone | False | 11-07 18:00 | 11-10 00:00 | 11-08 09:00 | b 2026111006 | 0 | 0 | 0/19 | False |
| 31891122a8d0 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-12 00:00 | a 2026111112 | 1 | 1 | 7/13 | False |
| 9518a6c054ff | tropical_cyclone | False | 11-06 06:00 | 11-11 12:00 | 11-11 15:00 | b 2026111118 | 0 | 0 | 0/43 | False |
| 128092256c62 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-14 00:00 | a 2026111200 | 1 | 1 | 9/13 | False |
| 31f05e7fba70 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-14 00:00 | a 2026111200 | 1 | 1 | 9/13 | False |
| dcdf246a84eb | tropical_cyclone | False | 11-02 12:00 | 11-12 12:00 | 11-12 06:00 | b 2026111218 | 0 | 0 | 0/81 | False |
| 31891122a8d0 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-12 00:00 | b 2026111306 | 0 | 0 | 0/13 | False |
| 128092256c62 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-14 00:00 | b 2026111406 | 0 | 0 | 0/13 | False |
| 31f05e7fba70 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-14 00:00 | b 2026111406 | 0 | 0 | 0/13 | False |
| a1d6b391e4d8 | tropical_cyclone | True | 11-15 06:00 | 11-17 00:00 | 11-17 03:00 | a 2026111606 | 1 | 1 | 15/15 | False |
| a1d6b391e4d8 | tropical_cyclone | True | 11-15 06:00 | 11-17 00:00 | 11-17 03:00 | b 2026111706 | 1 | 1 | 15/15 | False |
| 00dc299bfcbd | midlat_cyclone | False | 10-14 06:00 | 10-17 18:00 | 10-16 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| 013826c5379e | tropical_cyclone | False | 08-21 00:00 | 08-28 06:00 | 08-28 09:00 | c 2026111900 | 0 | 0 | 0/59 | False |
| 06be9280df4b | midlat_cyclone | False | 08-26 18:00 | 08-30 18:00 | 08-31 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |
| 08fac5554fb4 | midlat_cyclone | False | 10-22 12:00 | 10-25 00:00 | 10-26 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 0978cee0a3c4 | midlat_cyclone | False | 10-27 12:00 | 10-31 00:00 | 10-31 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| 097d779c88fd | tropical_cyclone | False | 09-25 00:00 | 10-11 12:00 | 10-11 12:00 | c 2026111900 | 0 | 0 | 0/133 | False |
| 09a98560b2ac | tropical_cyclone | False | 10-18 18:00 | 10-22 18:00 | 10-22 21:00 | c 2026111900 | 0 | 0 | 0/33 | False |
| 0b91bcef5da1 | midlat_cyclone | False | 09-22 00:00 | 09-26 06:00 | 09-26 00:00 | c 2026111900 | 0 | 0 | 0/18 | False |
| 0c221ac1eb33 | midlat_cyclone | False | 10-18 12:00 | 10-22 18:00 | 10-21 00:00 | c 2026111900 | 0 | 0 | 0/18 | False |
| 0dcf6b69bdc0 | midlat_cyclone | False | 08-29 06:00 | 09-03 00:00 | 09-03 00:00 | c 2026111900 | 0 | 0 | 0/20 | False |
| 128092256c62 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-14 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 1324816002ea | midlat_cyclone | False | 10-28 12:00 | 10-31 12:00 | 10-30 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 1614fd3a0005 | midlat_cyclone | False | 10-27 00:00 | 10-31 12:00 | 11-01 00:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| 168380aab44a | midlat_cyclone | False | 08-27 00:00 | 08-29 06:00 | 08-30 00:00 | c 2026111900 | 0 | 0 | 0/10 | False |
| 1a992ebc5446 | tropical_cyclone | False | 09-10 12:00 | 10-11 12:00 | 10-10 15:00 | c 2026111900 | 0 | 0 | 0/249 | False |
| 1f492d08bdce | midlat_cyclone | False | 10-24 00:00 | 10-29 06:00 | 10-29 00:00 | c 2026111900 | 0 | 0 | 0/22 | False |
| 2106cb080da3 | midlat_cyclone | False | 09-01 00:00 | 09-06 06:00 | 09-04 00:00 | c 2026111900 | 0 | 0 | 0/22 | False |
| 21f32fd04468 | midlat_cyclone | False | 09-11 06:00 | 09-15 12:00 | 09-16 00:00 | c 2026111900 | 0 | 0 | 0/18 | False |
| 2283fe825f9b | midlat_cyclone | False | 09-27 12:00 | 09-30 00:00 | 09-30 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 27222b8db889 | tropical_cyclone | False | 10-04 18:00 | 10-11 12:00 | 10-10 12:00 | c 2026111900 | 0 | 0 | 0/55 | False |
| 2d3a927184d6 | tropical_cyclone | False | 08-31 06:00 | 09-03 00:00 | 09-03 03:00 | c 2026111900 | 0 | 0 | 0/23 | False |
| 2f1a78bbfd6d | tropical_cyclone | False | 08-21 00:00 | 08-25 06:00 | 08-24 03:00 | c 2026111900 | 0 | 0 | 0/35 | False |
| 2fa8f8b34bec | tropical_cyclone | False | 10-23 12:00 | 11-05 06:00 | 11-05 09:00 | c 2026111900 | 0 | 0 | 0/103 | False |
| 3006ff3558ba | tropical_cyclone | False | 09-07 18:00 | 09-15 12:00 | 09-14 21:00 | c 2026111900 | 0 | 0 | 0/63 | False |
| 31891122a8d0 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-12 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 31f05e7fba70 | midlat_cyclone | False | 11-10 00:00 | 11-13 00:00 | 11-14 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 32230442769b | midlat_cyclone | False | 09-08 00:00 | 09-11 00:00 | 09-10 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 3684807ce119 | midlat_cyclone | False | 09-05 06:00 | 09-08 06:00 | 09-09 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 37bdab61dce6 | tropical_cyclone | False | 08-25 18:00 | 09-11 00:00 | 09-11 03:00 | c 2026111900 | 0 | 0 | 0/131 | False |
| 3b0231513926 | tropical_cyclone | False | 09-26 18:00 | 09-30 12:00 | 09-30 15:00 | c 2026111900 | 0 | 0 | 0/31 | False |
| 3b4108d8a8d0 | midlat_cyclone | False | 10-27 12:00 | 10-31 00:00 | 10-30 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| 3d3fbf19f543 | midlat_cyclone | False | 10-12 00:00 | 10-18 18:00 | 10-19 00:00 | c 2026111900 | 0 | 0 | 0/28 | False |
| 3f37cf13808f | midlat_cyclone | False | 10-31 18:00 | 11-03 18:00 | 11-04 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 41ca1a5cf33c | tropical_cyclone | False | 08-28 18:00 | 09-03 06:00 | 09-02 06:00 | c 2026111900 | 0 | 0 | 0/45 | False |
| 41f15548d202 | midlat_cyclone | False | 11-01 12:00 | 11-03 18:00 | 11-04 00:00 | c 2026111900 | 0 | 0 | 0/10 | False |
| 450bed8d1975 | midlat_cyclone | False | 10-21 06:00 | 10-27 06:00 | 10-25 00:00 | c 2026111900 | 0 | 0 | 0/25 | False |
| 452175874da2 | midlat_cyclone | False | 09-12 12:00 | 09-16 18:00 | 09-16 00:00 | c 2026111900 | 0 | 0 | 0/18 | False |
| 465d6123facd | midlat_cyclone | False | 10-17 00:00 | 10-23 12:00 | 10-20 00:00 | c 2026111900 | 0 | 0 | 0/27 | False |
| 4783c1aa0dce | tropical_cyclone | False | 10-20 06:00 | 10-24 00:00 | 10-23 15:00 | c 2026111900 | 0 | 0 | 0/31 | False |
| 47aeeb1f9b54 | midlat_cyclone | False | 10-18 00:00 | 10-20 06:00 | 10-21 00:00 | c 2026111900 | 0 | 0 | 0/10 | False |
| 47c1418e0020 | midlat_cyclone | False | 09-30 06:00 | 10-07 06:00 | 10-02 00:00 | c 2026111900 | 0 | 0 | 0/29 | False |
| 49bd937a0cd9 | midlat_cyclone | False | 08-30 12:00 | 09-01 12:00 | 09-02 00:00 | c 2026111900 | 0 | 0 | 0/9 | False |
| 4b560d4b31f5 | tropical_cyclone | False | 09-23 06:00 | 10-01 06:00 | 09-30 15:00 | c 2026111900 | 0 | 0 | 0/65 | False |
| 4dd3a55a14b9 | tropical_cyclone | False | 08-27 00:00 | 08-31 00:00 | 08-29 21:00 | c 2026111900 | 0 | 0 | 0/33 | False |
| 4fc0d133541c | midlat_cyclone | False | 09-17 00:00 | 09-19 12:00 | 09-19 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 51a6978b7d36 | midlat_cyclone | False | 08-28 12:00 | 09-01 06:00 | 09-02 00:00 | c 2026111900 | 0 | 0 | 0/16 | False |
| 52327cd61784 | midlat_cyclone | False | 10-24 00:00 | 10-26 12:00 | 10-26 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 524d59ac0fe7 | tropical_cyclone | False | 08-21 00:00 | 08-26 06:00 | 08-25 12:00 | c 2026111900 | 0 | 0 | 0/43 | False |
| 53edb6c0a38a | midlat_cyclone | False | 09-14 18:00 | 09-16 18:00 | 09-17 00:00 | c 2026111900 | 0 | 0 | 0/9 | False |
| 5a5bb77e19b3 | tropical_cyclone | False | 08-26 06:00 | 08-31 00:00 | 08-30 15:00 | c 2026111900 | 0 | 0 | 0/39 | False |
| 5abc5a4b6214 | midlat_cyclone | False | 08-28 18:00 | 08-31 06:00 | 09-01 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 5ae8b7da2120 | midlat_cyclone | False | 10-11 00:00 | 10-13 12:00 | 10-14 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 5e360d5663b4 | tropical_cyclone | False | 10-04 12:00 | 10-16 18:00 | 10-16 15:00 | c 2026111900 | 0 | 0 | 0/99 | False |
| 5e5ce42a239e | tropical_cyclone | False | 10-24 06:00 | 10-27 00:00 | 10-26 09:00 | c 2026111900 | 0 | 0 | 0/23 | False |
| 68ec790015c7 | tropical_cyclone | False | 08-21 00:00 | 08-25 18:00 | 08-25 21:00 | c 2026111900 | 0 | 0 | 0/39 | False |
| 6a435f91d896 | midlat_cyclone | False | 09-18 12:00 | 09-21 06:00 | 09-22 00:00 | c 2026111900 | 0 | 0 | 0/12 | False |
| 7171619838ce | tropical_cyclone | False | 10-16 06:00 | 10-19 06:00 | 10-19 09:00 | c 2026111900 | 0 | 0 | 2/25 | False |
| 71cbd7eeaf25 | midlat_cyclone | False | 08-31 00:00 | 09-02 06:00 | 09-02 00:00 | c 2026111900 | 0 | 0 | 0/10 | False |
| 720d16977a23 | midlat_cyclone | False | 09-30 18:00 | 10-04 00:00 | 10-05 00:00 | c 2026111900 | 0 | 0 | 0/14 | False |
| 72284f83d056 | midlat_cyclone | False | 10-30 12:00 | 11-03 12:00 | 11-04 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |
| 77a34212f688 | midlat_cyclone | False | 10-25 06:00 | 10-29 00:00 | 10-30 00:00 | c 2026111900 | 0 | 0 | 0/16 | False |
| 77a6e1faa78f | midlat_cyclone | False | 10-26 06:00 | 10-30 18:00 | 10-28 00:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| 7862dd70553e | midlat_cyclone | False | 10-11 06:00 | 10-14 00:00 | 10-15 00:00 | c 2026111900 | 0 | 0 | 0/12 | False |
| 786a036ca8d0 | tropical_cyclone | False | 09-22 18:00 | 09-24 00:00 | 09-24 03:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| 7cec41fec1ef | midlat_cyclone | False | 10-16 18:00 | 10-20 06:00 | 10-21 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| 83ebcc85928e | midlat_cyclone | False | 10-31 06:00 | 11-03 18:00 | 11-03 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| 874983a234bc | midlat_cyclone | False | 10-31 18:00 | 11-03 18:00 | 11-03 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| 8f747eb0052d | tropical_cyclone | False | 08-25 00:00 | 09-07 00:00 | 09-07 03:00 | c 2026111900 | 0 | 0 | 0/105 | False |
| 8f8538d47139 | tropical_cyclone | False | 09-30 00:00 | 10-07 06:00 | 10-07 09:00 | c 2026111900 | 0 | 0 | 0/59 | False |
| 8fce6c60fd09 | midlat_cyclone | False | 09-23 18:00 | 09-27 06:00 | 09-27 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| 8fd7374369ae | midlat_cyclone | False | 10-09 12:00 | 10-14 00:00 | 10-14 00:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| 9102f927da6b | midlat_cyclone | False | 10-11 12:00 | 10-16 00:00 | 10-16 00:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| 9227f3e04f69 | tropical_cyclone | False | 08-21 00:00 | 08-28 00:00 | 08-28 03:00 | c 2026111900 | 0 | 0 | 0/57 | False |
| 92b1a98ceba6 | midlat_cyclone | False | 09-01 18:00 | 09-04 12:00 | 09-05 00:00 | c 2026111900 | 0 | 0 | 0/12 | False |
| 95180b67568a | tropical_cyclone | False | 08-21 12:00 | 08-27 00:00 | 08-27 00:00 | c 2026111900 | 0 | 0 | 0/45 | False |
| 9518a6c054ff | tropical_cyclone | False | 11-06 06:00 | 11-11 12:00 | 11-11 15:00 | c 2026111900 | 0 | 0 | 0/43 | False |
| 98615c5da0fa | midlat_cyclone | False | 09-07 06:00 | 09-11 12:00 | 09-10 00:00 | c 2026111900 | 0 | 0 | 0/18 | False |
| 9ad7027c17dc | midlat_cyclone | False | 10-21 06:00 | 10-24 12:00 | 10-25 00:00 | c 2026111900 | 0 | 0 | 0/14 | False |
| 9b29aec3769f | tropical_cyclone | False | 09-15 12:00 | 09-22 12:00 | 09-22 15:00 | c 2026111900 | 0 | 0 | 0/57 | False |
| a0ce3336ca10 | midlat_cyclone | False | 08-23 18:00 | 08-26 18:00 | 08-27 00:00 | c 2026111900 | 0 | 0 | 0/13 | False |
| a1d6b391e4d8 | tropical_cyclone | True | 11-15 06:00 | 11-17 00:00 | 11-17 03:00 | c 2026111900 | 1 | 1 | 15/15 | False |
| a564bf02954f | tropical_cyclone | False | 10-11 06:00 | 10-20 00:00 | 10-20 00:00 | c 2026111900 | 0 | 0 | 0/71 | False |
| a6a3706f59d4 | midlat_cyclone | False | 10-13 00:00 | 10-17 00:00 | 10-17 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |
| a966ddfde13c | tropical_cyclone | False | 08-29 18:00 | 09-08 12:00 | 09-08 15:00 | c 2026111900 | 0 | 0 | 0/79 | False |
| aa15e18db971 | midlat_cyclone | False | 09-02 18:00 | 09-05 06:00 | 09-05 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| afdd2de7aabd | tropical_cyclone | False | 10-14 12:00 | 10-26 00:00 | 10-26 03:00 | c 2026111900 | 0 | 0 | 0/93 | False |
| b32081a3b590 | midlat_cyclone | False | 10-31 06:00 | 11-03 12:00 | 11-03 00:00 | c 2026111900 | 0 | 0 | 0/14 | False |
| b4f987194bfa | midlat_cyclone | False | 10-10 18:00 | 10-13 06:00 | 10-14 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| b6b2e09f3b99 | midlat_cyclone | False | 10-11 18:00 | 10-16 18:00 | 10-16 00:00 | c 2026111900 | 0 | 0 | 0/21 | False |
| bd06f162e627 | midlat_cyclone | False | 09-07 00:00 | 09-10 06:00 | 09-11 00:00 | c 2026111900 | 0 | 0 | 0/14 | False |
| be183ee601c7 | tropical_cyclone | False | 09-16 18:00 | 09-27 18:00 | 09-27 21:00 | c 2026111900 | 0 | 0 | 0/89 | False |
| bf564a80ac5f | midlat_cyclone | False | 10-27 00:00 | 10-31 00:00 | 10-30 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |
| bfe1bfdb56a1 | midlat_cyclone | False | 10-21 18:00 | 10-27 00:00 | 10-28 00:00 | c 2026111900 | 0 | 0 | 0/22 | False |
| c066286e3678 | midlat_cyclone | False | 10-14 00:00 | 10-18 12:00 | 10-16 00:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| c0779432587c | midlat_cyclone | False | 10-03 00:00 | 10-07 18:00 | 10-07 00:00 | c 2026111900 | 0 | 0 | 0/20 | False |
| c0f5a145a343 | midlat_cyclone | False | 09-04 06:00 | 09-08 00:00 | 09-08 00:00 | c 2026111900 | 0 | 0 | 0/16 | False |
| c29831141981 | tropical_cyclone | False | 10-26 00:00 | 10-29 06:00 | 10-28 15:00 | c 2026111900 | 0 | 0 | 0/27 | False |
| c4b0d2570c94 | midlat_cyclone | False | 11-05 00:00 | 11-07 06:00 | 11-08 00:00 | c 2026111900 | 0 | 0 | 0/10 | False |
| c6c77cff2115 | tropical_cyclone | False | 10-18 18:00 | 10-30 00:00 | 10-29 15:00 | c 2026111900 | 0 | 0 | 0/91 | False |
| cca0c2eb21f6 | midlat_cyclone | False | 11-05 00:00 | 11-08 18:00 | 11-08 00:00 | c 2026111900 | 0 | 0 | 0/16 | False |
| cf0d74b427ab | midlat_cyclone | False | 09-22 12:00 | 09-24 18:00 | 09-24 00:00 | c 2026111900 | 0 | 0 | 0/10 | False |
| d0641847f100 | tropical_cyclone | False | 09-01 06:00 | 09-09 12:00 | 09-05 09:00 | c 2026111900 | 0 | 0 | 0/67 | False |
| d209cb72530b | tropical_cyclone | False | 11-07 18:00 | 11-10 00:00 | 11-08 09:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| d30504cba48e | midlat_cyclone | False | 09-17 18:00 | 09-20 06:00 | 09-21 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| dcdf246a84eb | tropical_cyclone | False | 11-02 12:00 | 11-12 12:00 | 11-12 06:00 | c 2026111900 | 0 | 0 | 0/81 | False |
| dd9825d6b56e | tropical_cyclone | False | 09-17 12:00 | 09-30 00:00 | 09-30 00:00 | c 2026111900 | 0 | 0 | 0/101 | False |
| de83ed02806d | tropical_cyclone | False | 11-01 00:00 | 11-03 06:00 | 11-03 09:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| de869c0ce1f4 | midlat_cyclone | False | 09-11 06:00 | 09-19 06:00 | 09-14 00:00 | c 2026111900 | 0 | 0 | 0/33 | False |
| de9fc11c9cef | midlat_cyclone | False | 09-19 06:00 | 09-22 18:00 | 09-23 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| e2ef32e60ed5 | midlat_cyclone | False | 10-28 12:00 | 10-31 06:00 | 11-01 00:00 | c 2026111900 | 0 | 0 | 0/12 | False |
| e4d8a743f5a0 | tropical_cyclone | False | 10-05 00:00 | 10-13 18:00 | 10-13 21:00 | c 2026111900 | 0 | 0 | 0/71 | False |
| e57f7ebfdf54 | midlat_cyclone | False | 08-23 12:00 | 08-25 12:00 | 08-26 00:00 | c 2026111900 | 0 | 0 | 0/9 | False |
| e7396df75419 | midlat_cyclone | False | 10-03 12:00 | 10-07 18:00 | 10-06 00:00 | c 2026111900 | 0 | 0 | 0/18 | False |
| eb5968513e1c | midlat_cyclone | False | 09-28 12:00 | 10-10 12:00 | 10-02 00:00 | c 2026111900 | 0 | 0 | 0/49 | False |
| ed1b9012b680 | tropical_cyclone | False | 09-22 18:00 | 09-26 18:00 | 09-26 21:00 | c 2026111900 | 0 | 0 | 0/33 | False |
| f0e20c5dccfc | tropical_cyclone | False | 09-17 12:00 | 09-29 18:00 | 09-29 09:00 | c 2026111900 | 0 | 0 | 0/99 | False |
| f2d932286433 | tropical_cyclone | False | 08-24 00:00 | 08-31 12:00 | 08-29 09:00 | c 2026111900 | 0 | 0 | 0/61 | False |
| f3727a8efbc6 | midlat_cyclone | False | 10-19 12:00 | 10-22 00:00 | 10-22 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| f37b74827ecb | midlat_cyclone | False | 10-20 06:00 | 10-24 18:00 | 10-25 00:00 | c 2026111900 | 0 | 0 | 0/19 | False |
| f3cd2c1042fc | midlat_cyclone | False | 09-11 00:00 | 09-14 12:00 | 09-14 00:00 | c 2026111900 | 0 | 0 | 0/15 | False |
| f460b6e46636 | midlat_cyclone | False | 10-25 12:00 | 10-29 12:00 | 10-29 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |
| fa72d5dcb4e0 | midlat_cyclone | False | 08-31 00:00 | 09-04 00:00 | 09-05 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |
| faa3a35f6047 | midlat_cyclone | False | 10-24 12:00 | 10-27 00:00 | 10-27 00:00 | c 2026111900 | 0 | 0 | 0/11 | False |
| fcd3ce47c725 | midlat_cyclone | False | 09-30 06:00 | 10-04 06:00 | 10-03 00:00 | c 2026111900 | 0 | 0 | 0/17 | False |

pageerrors: []

## Repro conclusion (pre-fix code, captured 2026-11-19)

* The one REAL violation is the flagged storm `a1d6b391e4d8` (life.ongoing): its SVG mark + label + full
  track line were drawn for 8 frames (2026111706..2026111900) past its last fix (2026111700) — in the global
  view, the TC tab and region views alike. Root cause: `expiredAt()` returned false whenever
  `lifeOf(ev).ongoing`, so the storm never expired; the window end is 2026-11-19 and the producer flags every
  dissipation-unconfirmed storm, so each weekly build ships one storm that lingers this way.
* All other rows above show `marks=0 labels=0` with canvas `hits>0`: an A/B (the same frame loaded with vs
  without that event's timeline entry — tmp/ab.py) produced IDENTICAL pixels with and without the event, i.e.
  those hits are OTHER storms' lines/fields under the probe points, not residue. No non-ongoing event drew
  anything past max(last fix, end) on this data — the wind field and the marks already obeyed that half of
  the rule; `dissipatedAt` / missing `life` / the tab and region paths did not reproduce.
* The hover-preview ghost (`draw()` → layoutStorm(ghost=true)) re-drew the WHOLE track of an expired storm at
  any frame while its event-list row was hovered — a second lingering path, fixed with the same gate.
* Verification after the fix: verify.md (same sweep, same harness) — 0 real violations of 1364 observations,
  #gm-tracks holds 0 opaque px at the window-end frame (every storm's cap is before it), the ghost probe drew
  0 pixels for all 124 events, and before/after crops at the storm's own last fix show the ring+label gone
  (before-zoom / after-zoom, plus the 4-viewport × light/dark matrix).
