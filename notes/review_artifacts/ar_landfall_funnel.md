# AR landfall funnel — why landfalling ARs are not events
science-review (successor 3), 2026-11-09. Owner HIGH report: "some ARs clearly make landfall but aren't being considered events."
Window: 2026-08-11 .. 2026-11-09 (336 six-hourly frames, 90-day). Read-only on production outputs; scripts `scripts/review/sr14*.py`; full JSON on the box at `/tmp/sr14_funnel.json`.

## Headline
Nothing is silently broken — every rejection is attributable to a named owner rule. The complaint traces to **four rules, in order of impact**:
1. **`landfall_days >= 3` curation floor** — kills 37 of 65 AR candidates, several with IVT 830–980 and 100–3559 land px/day (the "clearly landfalling" cases).
2. **bbox span cap (60 deg)** — kills the single strongest AR of the window: eb42387c81a3, IVT 1038, 8 landfall days, 3255 land px/day, a 90-deg-wide trans-Atlantic+Asia ribbon.
3. **severity quota (top 15)** — drops 9 floor-passing landfall ARs (ranks 15–23), incl. IVT 938 while shipping IVT 600–653 events.
4. **publish gate `peak_land_pixels < 500`** — silently drops 3 post-curation events (326, 453, and one merged product at 170). The 08:13 run wrote no decision entries and logged nothing for them; 15 kept -> 12 shipped was invisible without a forensic replay.

Additionally, the raw map shows **huge land-locked red blobs** (3k–5.3k daily-union land px: Urals/Kara, W Canada, Europe, one over the Indian monsoon). They die at **`ocean-start >= 0.5`** before ever becoming candidates (50 daily-union tracks: the IVT>=250 mask first appears over land, so there is no marine track history). Visually these are the most "obvious landfall" objects on the map and they are correctly rejected: they are not ARs making landfall, they are IVT maxima living over land (monsoon / cut-off lows), which the ocean-start rule exists to refuse.

No bugs found: TC screen clean (§I d2_pixel_audit, min 503.4 km to any TC over all 336 frames, independent IBTrACS); seam/0-360 contract holds (the naive-lon bug was in my own scan, fixed per ponytail 15:58Z; re-run changed only 7 attributions); tropical exclusion pixel-verified in §I; ERA5/mirror window dropped **zero** candidates; cross-type overlap dropped **zero** ARs this run — verified by replaying the production `rank_and_cap`: replay keeps exactly the 12 shipped + the 3 gate victims, nothing else.

## Funnel counts (before -> after each rule)
Raw tracking layer (`ar_tracks.json`, all 6h objects tracked in window):

| stage | before | after | dropped |
|---|---|---|---|
| tracked objects in window | — | 3959 | — |
| tropical exclusion (touch \|lat\|<15) | 3959 | 3484 | 475 |
| TC screen (IBTrACS buffer) | 3484 | 3350 | 134 (34 named storms; nearest retained-AR px to any TC: 503.4 km) |
| kept tracks touching land at some step | 3350 | 1470 | (1880 purely oceanic) |

(304 with >=100 land px; tracking-split class; the raw layer additionally hides <2-day specks), D4 ocean-start: 50, D3 landfall<2d: 10, D6 IVT>1500: 6, passes-but-not-a-candidate (IVT unmeasured at the 60-window mirror cap): 18. Daily counts are the OR-union of the four 6-hourly frames, exactly ar.py's daily mask (ar.py:265); px/land totals stay cumulative.

Candidate/event level (detection runs its own tracking; `events.json` catalogue 65 AR candidates -> `events_selected.json` 12):

| stage | before | after | dropped |
|---|---|---|---|
| AR candidates | 65 | 65 | — |
| curation floor | 65 | 26 | 39 (landfall_days<3: **37**, peak IVT<600: 1, lon-span>60: 1) |
| same-event merge | 26 | 24 | 2 absorbed (both cores survive) |
| cross-type overlap | 24 | 24 | 0 |
| severity quota top-15 | 24 | 15 | 9 (ranks 15–23) |
| publish gate (peak_land<500) | 15 | **12 shipped** | 3 (326, 453, merged product 170 px) |

Owner's "AR went 15 -> 10/12": curation's quota keeps 15, then the publish gate silently removes land-pixel-thin ones. This run: 12.

## Candidate table (65 rows, by peak IVT)
| id | dates | region (bbox centroid) | peak IVT | land px/day | landfall days | det. track | first rejecting rule |
|---|---|---|---|---|---|---|---|
| eb42387c81a3 | 08-07–08-15 | E Siberia/Okhotsk | 1038 | 3255 | 8 | 20260810T12_1115 | floor: span lat 39/lon 90 > 60 |
| 59c1cc948b66 | 08-18–08-21 | Japan/E Asia | 996 | 157 | 3 | 20260816T06_1489 | merge: absorbed into 457f181d79c5 |
| df7ad7ff2f51 | 10-26–10-28 | SE Australia/NZ | 980 | 191 | 2 | 20261017T00_5403 | floor: landfall_days 2 < 3 |
| ce2db73f4be4 | 08-23–08-26 | W Canada/Alaska | 973 | 453 | 3 | 20260822T12_1884 | publish gate: rejected (inferred peak_land<500; no gate record) |
| 457f181d79c5 | 08-15–08-21 | E Siberia/Okhotsk | 962 | 170 | 5 | 20260816T06_1489 | publish gate: merged product rejected (inferred) |
| f93e1481d3a6 | 09-17–09-21 | N Atlantic/UK-Scandinavia | 947 | 151 | 3 | 20260913T06_3424 | merge: absorbed into b71262d8884b |
| 25c768228369 | 09-26–09-28 | SE Australia/NZ | 943 | 211 | 2 | 20260925T00_4130 | floor: landfall_days 2 < 3 |
| bcc401cecaad | 10-13–10-16 | SE Australia/NZ | 938 | 174 | 3 | 20261013T06_5219 | quota: severity rank 17 (>15) |
| 4a9c67b01f25 | 10-07–10-11 | SE Australia/NZ | 865 | 3576 | 4 | 20261007T00_4869 | — (shipped) |
| bbbd78137ddd | 10-23–10-27 | SE Australia/NZ | 854 | 2748 | 4 | 20261017T00_5403 | — (shipped) |
| e86c13f47bc0 | 10-28–10-31 | S Pacific/Chile | 843 | 669 | 3 | 20261025T12_5882 | — (shipped) |
| 49a026787716 | 10-23–10-26 | W Canada/Alaska | 840 | 1066 | 2 | 20261014T06_5269 | floor: landfall_days 2 < 3 |
| adcdad404c32 | 09-01–09-05 | Japan/E Asia | 831 | 326 | 4 | 20260831T12_2596 | publish gate: rejected (inferred peak_land<500; no gate record) |
| dfa1d90b5618 | 10-05–10-07 | W Canada/Alaska | 828 | 262 | 2 | 20261005T00_4704 | floor: landfall_days 2 < 3 |
| 92c0f25d1d23 | 08-20–08-23 | W Canada/Alaska | 828 | 262 | 3 | 20260816T00_1481 | quota: severity rank 16 (>15) |
| eb65e5e99645 | 09-12–09-15 | N Atlantic/UK-Scandinavia | 810 | 905 | 3 | 20260912T18_3375 | — (shipped) |
| a05ba1092bf8 | 08-12–08-15 | E Siberia/Okhotsk | 807 | 119 | 2 | 20260810T12_1115 | floor: landfall_days 2 < 3 |
| 6b4dec9dc8db | 08-27–08-30 | Europe/W Russia | 792 | 1632 | 3 | 20260828T12_2349 | — (shipped) |
| 5dd38675f9c5 | 09-17–09-20 | SE Australia/NZ | 770 | 117 | 3 | 20260913T00_3414 | quota: severity rank 19 (>15) |
| 0df36ee11032 | 10-03–10-07 | N Atlantic/UK-Scandinavia | 770 | 709 | 4 | 20261005T18_4764 | — (shipped) |
| b71262d8884b | 09-17–09-21 | N Atlantic/UK-Scandinavia | 768 | 509 | 4 | 20260913T06_3424 | merge core: shipped as merged id 65243d05a247 |
| d8538ddf95c3 | 10-20–10-22 | W Canada/Alaska | 764 | 138 | 2 | 20261017T00_5381 | floor: landfall_days 2 < 3 |
| af07d13c1fcc | 09-02–09-06 | N Atlantic/UK-Scandinavia | 758 | 85 | 4 | 20260827T18_2280 | quota: severity rank 20 (>15) |
| 56b282629cf6 | 10-22–10-25 | N Atlantic/UK-Scandinavia | 756 | 870 | 3 | 20261023T00_5750 | — (shipped) |
| 31c8f72663bc | 09-28–10-01 | W Canada/Alaska | 746 | 712 | 2 | 20260923T12_4059 | floor: landfall_days 2 < 3 |
| bf07d11c8060 | 09-12–09-16 | (-27N,20E) | 728 | 1415 | 4 | 20260912T06_3337 | — (shipped) |
| 6772ca39f2aa | 10-22–10-26 | SE Australia/NZ | 724 | 117 | 3 | 20261017T00_5403 | quota: severity rank 21 (>15) |
| cabbca492f14 | 09-05–09-08 | SE Australia/NZ | 722 | 214 | 2 | 20260903T18_2830 | floor: landfall_days 2 < 3 |
| 029719d1e741 | 09-29–10-03 | SE Australia/NZ | 715 | 1554 | 4 | 20260927T18_4275 | — (shipped) |
| 06ceb4d8d4fe | 10-05–10-07 | Europe/W Russia | 711 | 3559 | 2 | 20261005T18_4764 | floor: landfall_days 2 < 3 |
| 3b75356cb253 | 10-20–10-22 | (42N,14E) | 710 | 325 | 2 | 20261019T12_5560 | floor: landfall_days 2 < 3 |
| 4eb239370f86 | 08-18–08-20 | W Canada/Alaska | 691 | 68 | 2 | 20260816T00_1481 | floor: landfall_days 2 < 3 |
| 1b946ca99dd7 | 08-15–08-18 | Japan/E Asia | 685 | 122 | 3 | 20260816T06_1489 | quota: severity rank 22 (>15) |
| c0cce779cf45 | 08-13–08-15 | N Atlantic/UK-Scandinavia | 671 | 102 | 2 | 20260813T06_1304 | floor: landfall_days 2 < 3 |
| 6894d2f02a79 | 09-03–09-06 | W Canada/Alaska | 670 | 142 | 2 | 20260902T00_2701 | floor: landfall_days 2 < 3 |
| bbb8c7e69fb0 | 08-26–08-29 | SE Australia/NZ | 665 | 79 | 3 | 20260824T06_2021 | quota: severity rank 23 (>15) |
| 13f6c231e80b | 10-26–10-28 | W Canada/Alaska | 661 | 194 | 2 | 20261023T06_5765 | floor: landfall_days 2 < 3 |
| 85636ad4eb4c | 08-17–08-21 | S Pacific/Chile | 659 | 396 | 4 | 20260814T00_1366 | quota: severity rank 15 (>15) |
| f0e4b36a2f34 | 08-24–08-27 | S Pacific/Chile | 654 | 240 | 2 | 20260818T00_1588 | floor: landfall_days 2 < 3 |
| 920f2f81e54d | 09-06–09-09 | N Atlantic/UK-Scandinavia | 653 | 717 | 3 | 20260907T12_3044 | — (shipped) |
| 951684fc2180 | 08-24–08-26 | W Canada/Alaska | 652 | 48 | 2 | 20260823T18_1973 | floor: landfall_days 2 < 3 |
| 9387df97e1c9 | 09-05–09-08 | W Canada/Alaska | 642 | 218 | 3 | 20260902T00_2701 | quota: severity rank 18 (>15) |
| ba7e51e5b5ee | 10-08–10-10 | W Canada/Alaska | 637 | 321 | 2 | 20261008T18_4959 | floor: landfall_days 2 < 3 |
| 1dac894cc2da | 08-22–08-24 | (42N,-75E) | 628 | 1118 | 2 | 20260822T12_1884 | floor: landfall_days 2 < 3 |
| a8fd05d45de2 | 08-13–08-15 | W Canada/Alaska | 627 | 326 | 2 | 20260809T00_1033 | floor: landfall_days 2 < 3 |
| 8002c1ab3ba0 | 10-27–10-30 | W Canada/Alaska | 624 | 629 | 3 | 20261028T00_5994 | — (shipped) |
| 1155ec3c693b | 08-19–08-21 | S Pacific/Chile | 610 | 41 | 2 | 20260814T00_1366 | floor: landfall_days 2 < 3 |
| 7f229f8a12e7 | 10-29–10-31 | Japan/E Asia | 600 | 223 | 2 | 20261029T00_6030 | floor: landfall_days 2 < 3 |
| e2ce9e17047c | 10-30–11-01 | SE Australia/NZ | 587 | 116 | 2 | 20261031T00_6181 | floor: landfall_days 2 < 3 |
| 7917221d5bff | 10-22–10-24 | N Atlantic/UK-Scandinavia | 585 | 22 | 2 | 20261021T00_5631 | floor: landfall_days 2 < 3 |
| e4974aa9b801 | 10-07–10-10 | (42N,13E) | 581 | 131 | 2 | 20261007T18_4895 | floor: landfall_days 2 < 3 |
| 19fe7759a80c | 09-19–09-21 | S Pacific/Chile | 574 | 222 | 2 | 20260919T18_3874 | floor: landfall_days 2 < 3 |
| 31146c88b22a | 09-18–09-21 | SE Australia/NZ | 571 | 164 | 3 | 20260919T00_3809 | floor: peak IVT 571 < 600 |
| e8569aee2f47 | 09-11–09-13 | (53N,157E) | 565 | 70 | 2 | 20260911T12_3274 | floor: landfall_days 2 < 3 |
| 20bc7ed6cbd7 | 10-09–10-11 | W Canada/Alaska | 545 | 365 | 2 | 20261009T06_4974 | floor: landfall_days 2 < 3 |
| 6216cc9d8c38 | 09-19–09-21 | W Canada/Alaska | 542 | 20 | 2 | 20260918T12_3772 | floor: landfall_days 2 < 3 |
| 81135ea1bf93 | 08-14–08-16 | S Pacific/Chile | 515 | 103 | 2 | 20260814T18_1414 | floor: landfall_days 2 < 3 |
| 63054c18ba5b | 09-16–09-18 | Mexico/Gulf | 514 | 205 | 2 | 20260916T00_3620 | floor: landfall_days 2 < 3 |
| 100a3e1d2611 | 08-11–08-13 | (35N,-78E) | 510 | 39 | 2 | 20260811T00_1154 | floor: landfall_days 2 < 3 |
| c6af7c5e2bb7 | 08-11–08-14 | N Atlantic/UK-Scandinavia | 492 | 136 | 2 | 20260811T00_1152 | floor: landfall_days 2 < 3 |
| 3cfcf2788a56 | 08-20–08-22 | W Canada/Alaska | 491 | 113 | 2 | 20260816T00_1481 | floor: landfall_days 2 < 3 |
| 10e358116d4e | 08-16–08-18 | Arctic | 489 | 95 | 2 | 20260810T12_1115 | floor: landfall_days 2 < 3 |
| f48420ad6236 | 09-17–09-19 | Mexico/Gulf | 488 | 114 | 2 | 20260918T00_3748 | floor: landfall_days 2 < 3 |
| 29768efe9fe4 | 10-18–10-20 | Europe/W Russia | 484 | 28 | 2 | 20261018T18_5510 | floor: landfall_days 2 < 3 |
| 80fb28587d42 | 09-23–09-25 | W Canada/Alaska | 458 | 91 | 2 | 20260924T00_4080 | floor: landfall_days 2 < 3 |

## Ledger landfalling rejects (top 20 of 388 land-touching tracks with >=100 peak land px, daily-union px)
| track | dates | region | peak IVT (mirror) | peak land px/day | land days | first rejecting rule (track-level sim) |
|---|---|---|---|---|---|---|
| 20260825T06_764 | 0825–0828 | W Siberia/Kara | — | 5265 | 4 | D4 ocean-start 0.04 < 0.5 |
| 20260923T06_2217 | 0923–0925 | Europe/W Russia | — | 4006 | 3 | D4 ocean-start 0.19 < 0.5 |
| 20260829T00_986 | 0829–0901 | W Canada/Alaska | — | 3699 | 4 | D4 ocean-start 0.00 < 0.5 |
| 20261020T06_3392 | 1020–1020 | Europe/W Russia | — | 3565 | 1 | D1 min days (tracked 1d < 2) |
| 20260911T12_1667 | 0911–0913 | W Canada/Alaska | — | 3372 | 3 | D4 ocean-start 0.12 < 0.5 |
| 20260910T06_1597 | 0910–0911 | W Siberia/Kara | — | 3182 | 2 | D4 ocean-start 0.38 < 0.5 |
| 20260821T00_532 | 0821–0823 | Europe/W Russia | — | 3074 | 3 | D4 ocean-start 0.18 < 0.5 |
| 20260820T00_479 | 0820–0821 | Europe/W Russia | — | 3068 | 2 | D4 ocean-start 0.00 < 0.5 |
| 20260825T12_777 | 0825–0826 | W Siberia/Kara | — | 2535 | 2 | D4 ocean-start 0.00 < 0.5 |
| 20261001T06_2540 | 1001–1001 | W Canada/Alaska | — | 2427 | 1 | D1 min days (tracked 1d < 2) |
| 20260818T06_386 | 0818–0819 | Europe/W Russia | — | 2262 | 2 | D4 ocean-start 0.00 < 0.5 |
| 20260831T12_1108 | 0831–0901 | W Canada/Alaska | — | 2050 | 2 | D4 ocean-start 0.00 < 0.5 |
| 20260928T00_2389 | 0928–0928 | Europe/W Russia | — | 1991 | 1 | D1 min days (tracked 1d < 2) |
| 20261016T00_3235 | 1016–1016 | Europe/W Russia | — | 1958 | 1 | D1 min days (tracked 1d < 2) |
| 20261010T06_2965 | 1010–1010 | Europe/W Russia | — | 1887 | 1 | D1 min days (tracked 1d < 2) |
| 20260901T06_1157 | 0901–0903 | (-36N,-31E) | 1620 | 1866 | 2 | D6 IVT 1620 > 1500 |
| 20260811T06_36 | 0811–0814 | (-34N,-32E) | — | 1816 | 4 | D4 ocean-start 0.00 < 0.5 |
| 20260922T12_2193 | 0922–0923 | W Siberia/Kara | — | 1738 | 2 | D4 ocean-start 0.03 < 0.5 |
| 20260823T12_669 | 0823–0825 | Europe/W Russia | — | 1634 | 3 | D4 ocean-start 0.00 < 0.5 |
| 20260817T00_319 | 0817–0818 | Europe/W Russia | — | 1599 | 2 | D4 ocean-start 0.00 < 0.5 |
(IVT "—" = not mirror-measured; the 60-window mirror cap was reserved for tracks passing all D-rules. D4 rows have ocean fraction <0.5 on their first tracked day. Peak land px/day is the OR-union of the day's four 6-h frames, matching ar.py's daily mask.)

## Classification of each rejecting rule
| rule | verdict | evidence |
|---|---|---|
| tropical \|lat\|<15 touch | correct per owner rules | §I pixel audit; a3bb/97c52/1ff04 genuine drops |
| TC screen | correct; no 0-360 mix-up | d2_pixel_audit CLEAN 503.4 km, independent IBTrACS |
| ocean-start (ocean frac >=0.5 on start day) | correct per rules; scientifically sound | land-locked IVT maxima (monsoon 24N/82E, Kara/Ural cut-off lows) are not AR landfalls; keep |
| duration (track >=2d; candidate dur [2,14]) | correct; tracking-split class real but benign at event level | 304 one-day land-touching fragments, but detection-window merge reassembles them (06ceb/0df36e, a05ba/eb4238, df7ad/bbbd share detection track ids) |
| landfall_days >=3 floor | **rule too strict for owner intent — confirm with owner** | 37 candidates incl. IVT 980/943/840/828 with 100–3559 land px/day; the #1 answer to the complaint |
| span <=60 deg cap | **rule too strict — deletes the window's strongest AR** | eb42387c81a3 (IVT 1038, 8 landfall days, 3255 px/day, 90 deg). Options: exempt landfall_days>=3 events, raise cap, or split multi-basin ribbons |
| severity quota (15) | correct per rules; flag the ranking formula | IVT 938/828/770 dropped at ranks 16–23 while IVT 600–653 ship — severity is land/area/duration-weighted, not IVT-weighted |
| publish gate: peak_land_pixels < min_mask_gridpoints (500) | correct per rules, **silently applied this window** | adcd 326, ce2db7 453, merged a6126c 170; the shipped curation.json predates gate logging — pipeline.py:533-540 now writes {stage:publish_gate,id,problem} per rejection; next run makes this row non-inferred |
| cross-type overlap / ERA5-mirror window | no effect this window | replayed production rank_and_cap; full-window scan: 0 drops |
| land mask | correct | same production land_mask() used by detection, tracking, and this audit |
| seam/longitude handling | correct in production | bug was in my audit script, fixed (ponytail 15:58Z) |

## Maps (notes/screenshots/)
- sr-arfunnel-1-eb42387c81a3.png — strongest AR of the window, 9208 window-land px at peak step, dropped only by the span cap
- sr-arfunnel-2-06ceb4d8d4fe.png — same physical UK AR as shipped 0df36ee11032; the second window fails landfall>=3 (benign fragment)
- sr-arfunnel-3-49a026787716.png — W Canada, 1066 land px/day, landfall 2<3
- sr-arfunnel-4-bcc401cecaad.png — SE Australia, IVT 938, dead by severity quota
- sr-arfunnel-5-df7ad7ff2f51.png — SE Australia/NZ, IVT 980, landfall 2<3

Ribbons drawn from the same ERA5-mirror IVT>=250 field the pipeline masks on; land from the production land_mask().

## Recommendations (owner decisions; no code changed — read-only audit)
1. Confirm whether the `landfall_days` floor should be 2 instead of 3 — it is the dominant landfall killer (37/65). Floor=2 would admit ~9–10 landfalling events inside the 15-cap (several currently also quota-blocked).
2. Exempt land-rich ARs from the 60-deg span cap (or split ribbons at sub-basin boundaries); as-is it deletes the window's #1 AR by IVT.
3. (DONE in code, pending next run) Publish gate now ERROR-logs every rejection and records {stage:publish_gate,id,problem} in curation.json (pipeline.py:533-540), so 15->12 is auditable without a forensic replay.
4. Leave ocean-start, tropical exclusion, TC screen, and the land mask as they are — verified correct; the land-locked blobs they kill are not AR landfalls (the Indian-monsoon blob on the raw map is the clearest example).
5. If the land-locked raw-map blobs are what prompted the complaint, consider (design lane) de-emphasizing AR pixels over land rather than changing science rules.
