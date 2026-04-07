import dataclasses
import json
import requests

from cereal import car, custom
from openpilot.system.hardware import HARDWARE
from openpilot.system.version import get_build_metadata

from openpilot.frogpilot.common import frogpilot_utilities, frogpilot_variables

BASE_URL = "https://nominatim.openstreetmap.org"

MINIMUM_POPULATION = 100_000


def get_city_center(latitude, longitude):
  result = (0.0, 0.0, "N/A", "N/A", "N/A")

  if latitude == 0 and longitude == 0:
    return result

  try:
    with requests.Session() as session:
      session.headers.update({
        "Accept-Language": "en",
        "User-Agent": "frogpilot-city-center-checker/1.0 (https://github.com/FrogAi/FrogPilot)",
      })

      reverse_params = {"addressdetails": 1, "format": "jsonv2", "lat": latitude, "lon": longitude, "layer": "address", "zoom": 10}
      response = session.get(f"{BASE_URL}/reverse", params=reverse_params, timeout=10)
      response.raise_for_status()
      address = response.json().get("address", {})

      city_name = address.get("city") or address.get("town") or address.get("village") or address.get("hamlet")
      state_name = address.get("state") or address.get("province") or address.get("region") or address.get("state_district") or "N/A"
      country_name = address.get("country", "N/A")
      country_code = (address.get("country_code") or "").lower()

      if city_name:
        city_query_params = {"addressdetails": 1, "extratags": 1, "featureType": "city", "format": "jsonv2", "limit": 10, "city": city_name}
        if country_name != "N/A":
          city_query_params["country"] = country_name
        if state_name != "N/A":
          city_query_params["state"] = state_name
        if country_code:
          city_query_params["countrycodes"] = country_code

        response = session.get(f"{BASE_URL}/search", params=city_query_params, timeout=10)
        response.raise_for_status()
        city_results = response.json()

        for city_result in city_results:
          population_raw = str((city_result.get("extratags") or {}).get("population", "0")).split(";")[0].strip()
          population_digits = "".join(character for character in population_raw if character.isdigit())
          population = int(population_digits) if population_digits else 0
          if population < MINIMUM_POPULATION:
            continue

          city_address = city_result.get("address", {})
          city_state = city_address.get("state") or city_address.get("province") or city_address.get("region") or city_address.get("state_district")
          city_country = city_address.get("country")
          if city_country is None or city_country.casefold() != country_name.casefold():
            continue
          if state_name != "N/A" and (city_state is None or city_state.casefold() != state_name.casefold()):
            continue

          city_latitude = city_result.get("lat")
          city_longitude = city_result.get("lon")
          if city_latitude is not None and city_longitude is not None:
            selected_city_name = city_address.get("city") or city_address.get("town") or city_address.get("village") or city_address.get("hamlet") or city_name
            result = (float(city_latitude), float(city_longitude), selected_city_name, state_name, country_name)
            break

      if result == (0.0, 0.0, "N/A", "N/A", "N/A"):
        capital_query = f"{state_name} state capital" if country_code == "us" and state_name != "N/A" else f"capital of {state_name}, {country_name}" if state_name != "N/A" else f"capital of {country_name}"
        capital_query_params = {"addressdetails": 1, "extratags": 1, "featureType": "city", "format": "jsonv2", "limit": 5, "q": capital_query}
        if country_code:
          capital_query_params["countrycodes"] = country_code

        response = session.get(f"{BASE_URL}/search", params=capital_query_params, timeout=10)
        response.raise_for_status()
        capital_results = response.json()

        selected_capital = None
        for capital_result in capital_results:
          if capital_result is None:
            continue

          capital_address = capital_result.get("address", {})
          capital_state = (
            capital_address.get("state")
            or capital_address.get("province")
            or capital_address.get("region")
            or capital_address.get("state_district")
          )
          capital_country = capital_address.get("country")
          if capital_country is None or capital_country.casefold() != country_name.casefold():
            continue
          if state_name != "N/A" and (capital_state is None or capital_state.casefold() != state_name.casefold()):
            continue

          is_tagged_capital = (capital_result.get("extratags") or {}).get("capital") in ("administrative", "state", "yes")
          if is_tagged_capital:
            selected_capital = capital_result
            break
          if selected_capital is None:
            selected_capital = capital_result

        if selected_capital:
          selected_capital_address = selected_capital.get("address", {})
          capital_latitude = selected_capital.get("lat")
          capital_longitude = selected_capital.get("lon")
          if capital_latitude is not None and capital_longitude is not None:
            selected_city_name = (
              selected_capital_address.get("city")
              or selected_capital_address.get("town")
              or selected_capital_address.get("village")
              or selected_capital_address.get("hamlet")
              or selected_capital.get("display_name", "").split(",")[0]
            )
            result = (float(capital_latitude), float(capital_longitude), selected_city_name, state_name, country_name)

  except (requests.exceptions.RequestException, TypeError, ValueError):
    result = (0.0, 0.0, "N/A", "N/A", "N/A")

  return result


def send_stats(gps_position, params, frogpilot_toggles):
  if not frogpilot_utilities.is_url_pingable(f"{frogpilot_variables.FROGPILOT_API}"):
    return

  api_token, build_metadata, device_type, dongle_id = frogpilot_utilities.get_frogpilot_api_info()
  if not api_token or not dongle_id:
    return

  car_params = {}
  msg_bytes = params.get("CarParamsPersistent")
  if msg_bytes:
    with car.CarParams.from_bytes(msg_bytes) as CP:
      cp_dict = CP.to_dict()
      cp_dict.pop("carFw", None)
      cp_dict.pop("carVin", None)
      car_params = cp_dict

  frogpilot_car_params = {}
  frogpilot_msg_bytes = params.get("FrogPilotCarParamsPersistent")
  if frogpilot_msg_bytes:
    with custom.FrogPilotCarParams.from_bytes(frogpilot_msg_bytes) as FPCP:
      fpcp_dict = FPCP.to_dict()
      frogpilot_car_params = fpcp_dict

  frogpilot_stats = params.get("FrogPilotStats")

  location = gps_position or {}
  original_latitude = round(float(location.get("latitude", 0.0)), 3)
  original_longitude = round(float(location.get("longitude", 0.0)), 3)
  latitude, longitude, city, state, country = get_city_center(original_latitude, original_longitude)

  payload = {
    "api_token": api_token,
    "build_metadata": build_metadata,
    "device": device_type,
    "frogpilot_dongle_id": dongle_id,
    "model_scores": [],
    "user_stats": {
      "calibrated_lateral_acceleration": params.get("CalibratedLateralAcceleration"),
      "calibration_progress": params.get("CalibrationProgress"),
      "car_params": car_params,
      "city": city,
      "country": country,
      "device": device_type,
      "frogpilot_car_params": frogpilot_car_params,
      "frogpilot_dongle_id": dongle_id,
      "frogpilot_stats": frogpilot_stats,
      "latitude": latitude,
      "longitude": longitude,
      "state": state,
      "toggles": vars(frogpilot_toggles),
      "using_default_model": params.get("DrivingModel").endswith("_default"),
    },
  }

  for model_name, data in sorted(params.get("ModelDrivesAndScores").items()):
    drives = data.get("Drives", 0)
    score = data.get("Score", 0)

    if drives > 0:
      payload["model_scores"].append({
        "model_name": frogpilot_utilities.clean_model_name(model_name),
        "drives": int(drives),
        "score": int(score),
      })

  try:
    response = requests.post(
      f"{frogpilot_variables.FROGPILOT_API}/stats",
      json=payload,
      headers={"Content-Type": "application/json", "User-Agent": "frogpilot-api/1.0"},
      timeout=30,
    )
    response.raise_for_status()
    print("Successfully sent FrogPilot stats!")
  except requests.exceptions.RequestException as error:
    print(f"Failed to send stats: {error}")
