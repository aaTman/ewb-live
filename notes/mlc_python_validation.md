# mlc lane: pure-Python tracker vs the TempestExtremes PROOF

Window `2023-10-26 00Z .. 2023-11-30 18Z`, 144 frames of ERA5 MSLP at 0.25 deg (median step 6.0 h), grid 705x1440, host `ewb`, 16 cores, run 2026-11-17 13:05Z.

## Timing / memory

| stage | wall (s) |
|---|---|
| read 144 frames back from the extracted netCDFs | 3.9 |
| **python tracker** (detect + stitch + gap fill) | 26.9 (0.19 s/frame) |
| python qualification + event mapping | 29.2 |
| TempestExtremes DetectNodes (36 files) | 17.9 |
| TempestExtremes StitchNodes | 0.01 |
| python process peak RSS | 2000 MB (was 113 MB at start) |

## Track population (tracker output, before event screens)

| quantity | python | TempestExtremes |
|---|---|---|
| nodes | 3199 | 4006 |
| tracks >= 48 h | 138 | 145 |
| median lifetime (h) | 66.0 | 72.0 |
| median min MSLP (hPa) | 975.1 | 977.3 |
| deepest (hPa) | 926.7 | 926.7 |
| median displacement (km) | 1693.1 | 1700.4 |
| tracks >= 1000 km | 108 | 110 |
| bombs | 17 | 18 |
| mean |genesis lat| | 51.5 | 52.8 |

## Track agreement

* TE tracks matched by a python track: **133/145**
* matched fixes: 1771
* position error over matched fixes: median **0.00 deg** (0 km), p90 0.00 deg, max 17.61 deg

## Known-storm check: Storm Ciaran (landfall Finistere, 2 Nov 2023)

| | python | TempestExtremes |
|---|---|---|
| nearest fix to the landfall point (12 h window) | 2023-11-02 00:00:00 | 2023-11-02 00:00:00 |
| distance to the landfall point (km) | 172 | 172 |
| MSLP at that fix (hPa) | 954.6 | 954.6 |
| track min MSLP (hPa) | 954.0 | 954.0 |
| min at | 2023-11-02 06:00:00 | 2023-11-02 06:00:00 |
| lifetime (h) | 156 | 156 |
| displacement (km) | 3456 | 3456 |
| max 24 h fall (hPa) | -32.2 | -32.2 |
| bomb | True | True |
| bomb ratio | 1.52 | 1.52 |
| fixes | 27 | 27 |

## Python event screens (tracker output -> published events)

* tracks considered: 138
* events published: 77
  - `2023-10-26 00Z` -> `2023-10-31` 972 hPa, 114 h, 1167 km, genesis 56.0/-27.2 at 0 m
  - `2023-10-26 12Z` -> `2023-10-30` 955 hPa, 60 h, 1203 km, genesis -61.0/-46.5 at 0 m
  - `2023-10-27 06Z` -> `2023-10-31` 956 hPa, 96 h, 3334 km, genesis -55.0/26.8 at 0 m
  - `2023-10-27 06Z` -> `2023-10-30` 980 hPa, 60 h, 2988 km, genesis 46.5/14.2 at 0 m
  - `2023-10-27 18Z` -> `2023-11-03` 988 hPa, 132 h, 2404 km, genesis 43.8/174.0 at 0 m
  - `2023-10-28 00Z` -> `2023-10-30` 954 hPa, 48 h, 1427 km, genesis -56.5/110.5 at 0 m
  - `2023-10-28 06Z` -> `2023-10-30` 927 hPa, 48 h, 1123 km, genesis -67.8/-161.5 at 0 m
  - `2023-10-28 18Z` -> `2023-10-30` 966 hPa, 60 h, 1650 km, genesis -49.5/-68.8 at 0 m
  - `2023-10-29 12Z` -> `2023-11-01` 965 hPa, 78 h, 1959 km, genesis 51.2/-75.5 at 0 m
  - `2023-10-30 12Z` -> `2023-11-02` 946 hPa, 108 h, 1857 km, genesis -63.8/41.2 at 0 m
  - `2023-11-01 18Z` -> `2023-11-04` 954 hPa, 156 h, 3456 km, genesis 44.8/-52.5 at 0 m, **bomb**
  - `2023-11-02 00Z` -> `2023-11-04` 953 hPa, 78 h, 4470 km, genesis -62.5/-63.0 at 0 m
  - `2023-11-03 00Z` -> `2023-11-06` 960 hPa, 108 h, 5516 km, genesis 39.0/-66.5 at 0 m, **bomb**
  - `2023-11-03 00Z` -> `2023-11-06` 986 hPa, 72 h, 2533 km, genesis -35.0/-30.5 at 0 m
  - `2023-11-03 00Z` -> `2023-11-06` 977 hPa, 114 h, 4317 km, genesis 36.5/176.2 at 0 m
  - `2023-11-03 12Z` -> `2023-11-05` 978 hPa, 114 h, 3012 km, genesis 48.2/103.5 at 0 m
  - `2023-11-04 06Z` -> `2023-11-07` 978 hPa, 162 h, 4596 km, genesis -34.5/-53.2 at 0 m, **bomb**
  - `2023-11-04 18Z` -> `2023-11-09` 982 hPa, 90 h, 4102 km, genesis 48.8/28.0 at 0 m
  - `2023-11-05 06Z` -> `2023-11-08` 946 hPa, 84 h, 2613 km, genesis -64.5/-123.8 at 0 m
  - `2023-11-05 18Z` -> `2023-11-08` 984 hPa, 60 h, 1395 km, genesis 57.0/165.8 at 0 m
  - `2023-11-06 06Z` -> `2023-11-08` 955 hPa, 48 h, 3106 km, genesis -65.5/145.5 at 0 m
  - `2023-11-06 18Z` -> `2023-11-09` 974 hPa, 48 h, 1661 km, genesis -64.0/136.8 at 0 m
  - `2023-11-07 00Z` -> `2023-11-09` 954 hPa, 48 h, 1361 km, genesis 54.2/-46.8 at 0 m, **bomb**
  - `2023-11-07 06Z` -> `2023-11-09` 960 hPa, 48 h, 1624 km, genesis -57.2/86.0 at 0 m
  - `2023-11-07 06Z` -> `2023-11-10` 982 hPa, 96 h, 5213 km, genesis 34.5/119.5 at 0 m
  - `2023-11-08 12Z` -> `2023-11-12` 972 hPa, 168 h, 3652 km, genesis -48.2/-54.0 at 0 m
  - `2023-11-08 18Z` -> `2023-11-10` 959 hPa, 78 h, 1090 km, genesis 54.0/-164.0 at 0 m
  - `2023-11-09 06Z` -> `2023-11-15` 986 hPa, 174 h, 3482 km, genesis 59.5/36.8 at 0 m
  - `2023-11-09 18Z` -> `2023-11-13` 961 hPa, 54 h, 1087 km, genesis -67.5/29.0 at 0 m
  - `2023-11-10 06Z` -> `2023-11-12` 950 hPa, 54 h, 1864 km, genesis -66.0/153.2 at 0 m
  - `2023-11-10 18Z` -> `2023-11-14` 954 hPa, 54 h, 1450 km, genesis -60.0/121.0 at 0 m
  - `2023-11-11 00Z` -> `2023-11-15` 981 hPa, 114 h, 1700 km, genesis -42.2/-158.5 at 0 m
  - `2023-11-11 18Z` -> `2023-11-13` 956 hPa, 114 h, 4445 km, genesis 41.2/129.2 at 0 m, **bomb**
  - `2023-11-11 18Z` -> `2023-11-13` 987 hPa, 102 h, 1864 km, genesis 58.8/-119.2 at 0 m
  - `2023-11-11 18Z` -> `2023-11-14` 946 hPa, 78 h, 4048 km, genesis -55.5/-153.5 at 0 m
  - `2023-11-12 00Z` -> `2023-11-15` 985 hPa, 54 h, 1297 km, genesis 49.8/31.5 at 0 m
  - `2023-11-12 06Z` -> `2023-11-14` 953 hPa, 72 h, 1128 km, genesis -58.8/60.0 at 0 m
  - `2023-11-12 12Z` -> `2023-11-16` 976 hPa, 78 h, 2906 km, genesis -48.2/-68.0 at 0 m
  - `2023-11-13 06Z` -> `2023-11-15` 982 hPa, 48 h, 2984 km, genesis -40.2/42.2 at 0 m
  - `2023-11-13 06Z` -> `2023-11-15` 971 hPa, 54 h, 2209 km, genesis 54.2/-8.0 at 0 m
  - `2023-11-13 18Z` -> `2023-11-16` 964 hPa, 48 h, 1762 km, genesis -68.8/19.8 at 0 m
  - `2023-11-14 00Z` -> `2023-11-16` 963 hPa, 72 h, 3578 km, genesis 34.2/145.0 at 0 m, **bomb**
  - `2023-11-14 12Z` -> `2023-11-18` 984 hPa, 90 h, 2345 km, genesis 53.0/-117.0 at 0 m
  - `2023-11-15 06Z` -> `2023-11-18` 961 hPa, 120 h, 6052 km, genesis -55.0/173.8 at 0 m
  - `2023-11-15 12Z` -> `2023-11-18` 969 hPa, 48 h, 2322 km, genesis -51.5/152.0 at 0 m
  - `2023-11-15 18Z` -> `2023-11-18` 968 hPa, 66 h, 1790 km, genesis 61.2/-169.2 at 0 m
  - `2023-11-15 18Z` -> `2023-11-20` 976 hPa, 114 h, 3947 km, genesis 40.0/-50.2 at 0 m
  - `2023-11-16 18Z` -> `2023-11-21` 968 hPa, 78 h, 1816 km, genesis -66.5/40.0 at 0 m
  - `2023-11-17 06Z` -> `2023-11-20` 979 hPa, 60 h, 1487 km, genesis -52.8/-116.0 at 0 m
  - `2023-11-17 12Z` -> `2023-11-20` 956 hPa, 54 h, 1224 km, genesis -60.0/101.0 at 0 m
  - `2023-11-17 12Z` -> `2023-11-20` 969 hPa, 72 h, 2317 km, genesis 48.5/-90.8 at 0 m
  - `2023-11-17 18Z` -> `2023-11-19` 973 hPa, 48 h, 1241 km, genesis 40.8/134.2 at 0 m, **bomb**
  - `2023-11-18 06Z` -> `2023-11-21` 971 hPa, 48 h, 1997 km, genesis -67.2/-139.2 at 0 m
  - `2023-11-19 00Z` -> `2023-11-22` 972 hPa, 54 h, 1286 km, genesis -45.2/-4.8 at 0 m
  - `2023-11-19 06Z` -> `2023-11-22` 970 hPa, 48 h, 1205 km, genesis -49.5/-45.5 at 0 m
  - `2023-11-19 12Z` -> `2023-11-21` 940 hPa, 66 h, 1923 km, genesis 46.5/157.0 at 0 m, **bomb**
  - `2023-11-19 12Z` -> `2023-11-22` 965 hPa, 132 h, 3226 km, genesis -58.2/142.5 at 0 m
  - `2023-11-19 18Z` -> `2023-11-22` 962 hPa, 108 h, 3767 km, genesis 35.2/-177.2 at 0 m, **bomb**
  - `2023-11-21 06Z` -> `2023-11-24` 986 hPa, 54 h, 1616 km, genesis 45.5/53.5 at 0 m
  - `2023-11-21 06Z` -> `2023-11-24` 948 hPa, 114 h, 2608 km, genesis 44.8/162.8 at 0 m, **bomb**
  - `2023-11-21 18Z` -> `2023-11-24` 951 hPa, 72 h, 2129 km, genesis 69.8/-49.5 at 0 m
  - `2023-11-21 18Z` -> `2023-11-24` 966 hPa, 126 h, 2692 km, genesis 57.8/-124.0 at 0 m
  - `2023-11-22 06Z` -> `2023-11-24` 943 hPa, 66 h, 1196 km, genesis -54.8/22.5 at 0 m
  - `2023-11-22 12Z` -> `2023-11-25` 962 hPa, 132 h, 2847 km, genesis -61.2/-135.0 at 0 m
  - `2023-11-22 12Z` -> `2023-11-25` 953 hPa, 84 h, 3336 km, genesis -57.2/90.0 at 0 m, **bomb**
  - `2023-11-22 18Z` -> `2023-11-26` 978 hPa, 60 h, 2532 km, genesis -43.5/-29.0 at 0 m
  - `2023-11-23 00Z` -> `2023-11-26` 982 hPa, 84 h, 2174 km, genesis 45.0/-42.0 at 0 m
  - `2023-11-24 18Z` -> `2023-11-28` 985 hPa, 54 h, 1995 km, genesis -40.5/-32.0 at 0 m
  - `2023-11-25 00Z` -> `2023-11-27` 970 hPa, 84 h, 2689 km, genesis 54.2/-54.8 at 0 m
  - `2023-11-25 00Z` -> `2023-11-28` 975 hPa, 84 h, 5281 km, genesis -34.8/-27.5 at 0 m
  - `2023-11-25 06Z` -> `2023-11-28` 966 hPa, 54 h, 3077 km, genesis -55.5/-94.0 at 0 m
  - `2023-11-25 12Z` -> `2023-11-27` 959 hPa, 84 h, 1999 km, genesis 46.2/139.5 at 0 m, **bomb**
  - `2023-11-25 18Z` -> `2023-11-29` 949 hPa, 78 h, 1144 km, genesis -65.5/-122.5 at 0 m
  - `2023-11-25 18Z` -> `2023-11-28` 970 hPa, 78 h, 2150 km, genesis 40.5/23.8 at 0 m
  - `2023-11-26 06Z` -> `2023-11-29` 965 hPa, 84 h, 2030 km, genesis -57.0/75.0 at 0 m
  - `2023-11-29 12Z` -> `2023-12-01` 942 hPa, 90 h, 1929 km, genesis -68.0/153.8 at 0 m, **bomb**
  - `2023-11-29 12Z` -> `2023-12-01` 949 hPa, 48 h, 1700 km, genesis 44.5/156.5 at 0 m, **bomb**
