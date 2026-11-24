"""How the tropical-cyclone track errors on this site are computed.

Plain Python (math only). Run `python3 track_scoring.py` for a worked example.
The project's test suite checks this listing against the shipped scorer
(ewb_live/scoring/track_verif.py) and fails if the two disagree.

For one verified sample = (storm, model, init, lead):

  observed position  p_o   best-track fix at the valid time (IBTrACS)
  forecast position  p_f   the model's MSLP low, followed from the observed
                           position at the init (the storm must already be at
                           tropical-storm force, 34 kt, at the init)

  distance   d     great-circle km from p_o to p_f              TrackPositionError
  motion     m     observed heading, from the fixes 6 h before and 6 h after the
                   valid time (one-sided at the first / last fix)
  delta = bearing(p_o -> p_f) - heading(m), wrapped to [-180, 180)
  along  = d cos(delta)   > 0: forecast AHEAD of the observed storm (too fast)
  cross  = d sin(delta)   > 0: forecast to the RIGHT of the observed track
  along^2 + cross^2 = d^2.  A storm moving slower than 2 km/h has no direction:
  its along / cross are left out (its distance is kept).

  CentralPressureError = forecast MSLP at the low - best-track pressure (hPa);
  positive = forecast too weak. At 0.25 degrees this is a resolution number for
  strong storms (a model cannot draw an 890 hPa core), so read it as a bias.

  TrackMiss = 1 when the model has no closed low to follow at that step (two
  steps in a row without one lose the track for the rest of the run), else 0.
  A miss gets no error value: it is counted in the miss rate, never imputed.

Landfall scores (EWB's own landfall classes, for storms that make landfall only).
The observed landfall is the best-track crossing of the coast; the forecast
landfall is the model track's own crossing paired to it. A sample = (storm,
init); its lead_time_hours is the hours from the init to the OBSERVED landfall
(rounded to 6 h), so the cells share the track columns 24 / 48 / 72 / 96 / 120.

  landfall_displacement   great-circle km between the forecast and the observed
                          landfall point. Lower is better.
  landfall_time_me        forecast landfall time - observed landfall time (h);
                          positive = the forecast lands later than the storm did.
                          Read as a bias to 0, like the cross-track component.
  landfall_intensity_mae  |forecast MSLP - best-track pressure| at the landfall
                          point (hPa). Lower is better.
  landfall_miss           1 when an observed landfall lay ahead within the horizon
                          but the model produced no paired landfall (no landfall
                          forecast, the track was lost, or the timing was off by
                          more than 24 h), else 0. A miss is counted in the miss
                          rate, never imputed — the same rule as TrackMiss.
"""
import math

R_EARTH_KM = 6371.0
STATIONARY_KMH = 2.0


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing 1 -> 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians((lon2 - lon1 + 180.0) % 360.0 - 180.0)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x))


def along_cross(obs, fc, heading_deg, speed_kmh):
    """(distance, along, cross) in km; obs / fc are (lat, lon)."""
    d = distance_km(*obs, *fc)
    if speed_kmh < STATIONARY_KMH:
        return d, None, None
    delta = math.radians((bearing_deg(*obs, *fc) - heading_deg + 180.0) % 360.0 - 180.0)
    return d, d * math.cos(delta), d * math.sin(delta)


if __name__ == "__main__":
    # a storm moving due north at 20 km/h; the forecast storm is 100 km ahead and 50 km to the east
    obs = (15.0, -110.0)
    fc = (15.0 + 100.0 / 111.195, -110.0 + 50.0 / (111.195 * math.cos(math.radians(15.0))))
    d, along, cross = along_cross(obs, fc, heading_deg=0.0, speed_kmh=20.0)
    print(f"distance {d:.0f} km, along {along:+.0f} km (ahead), cross {cross:+.0f} km (right)")
