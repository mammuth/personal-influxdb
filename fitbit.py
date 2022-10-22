#!/usr/bin/python3
# Copyright 2022 Sam Steele
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import requests, sys, os, pytz
from datetime import datetime, date, timedelta
from config import *

if not FITBIT_CLIENT_ID or not FITBIT_CLIENT_SECRET:
    logging.error("FITBIT_CLIENT_ID or FITBIT_CLIENT_SECRET not set in config.py")
    sys.exit(1)
points = []

# todo use this in more methods?
def _get(url):
    try:
        response = requests.get(url, 
            headers={'Authorization': f'Bearer {FITBIT_ACCESS_TOKEN}', 'Accept-Language': FITBIT_LANGUAGE})
        response.raise_for_status()
        return response
    except requests.exceptions.HTTPError as err:
        logging.error("HTTP request failed: %s", err)
        if err.response.status_code == 429:
            # todo didn't test this yet
            retry_seconds = err.response.headers['Fitbit-Rate-Limit-Reset']
            retry_mins = retry_seconds * 60 if retry_seconds else '¯\_(ツ)_/¯'
            logging.error(f'You hit Fitbits rate limiting. You can try it again in {retry_mins} minutes. https://dev.fitbit.com/build/reference/web-api/developer-guide/application-design/#Rate-Limits')
        else:
            logging.error(err.response.text)
        sys.exit(1)

def fetch_data(category, type, end_date=None):
    date = end_date.strftime('%Y-%m-%d') if end_date else 'today'
    url = f'https://api.fitbit.com/1/user/-/{category}/{type}/date/{date}/1y.json'
    
    response = _get(url)

    data = response.json()
    logging.info(f"Got {type} from Fitbit")

    for day in data[category.replace('/', '-') + '-' + type]:
        points.append({
                "measurement": type,
                "time": LOCAL_TIMEZONE.localize(datetime.fromisoformat(day['dateTime'])).astimezone(pytz.utc).isoformat(),
                "fields": {
                    "value": float(day['value'])
                }
            })


def fetch_heartrate(date, intraday_api=True):
    # it uses the intraday API with 1m resolution for heart rate data. 
    # Unset intraday_api if you want to use the daily summary API to reduce rate limits

    if intraday_api is True:
        url = f'https://api.fitbit.com/1/user/-/activities/heart/date/{date.strftime("%Y-%m-%d")}/1d/1min.json'
    else:
        url = f'https://api.fitbit.com/1/user/-/activities/heart/date/{date.strftime("%Y-%m-%d")}/1m.json'
    try:
        # todo not do 1d but 1y? might time out I guess...
        response = requests.get(url, 
            headers={'Authorization': f'Bearer {FITBIT_ACCESS_TOKEN}', 'Accept-Language': FITBIT_LANGUAGE})
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        logging.error("HTTP request failed: %s", err)
        sys.exit(1)

    data = response.json()
    logging.info("Got heartrates from Fitbit")

    if 'activities-heart' not in data:
        logging.info(f'Skipping date {date}, it seems to have no heart rate data...')
        return

    for day in data['activities-heart']:
        if 'restingHeartRate' in day['value']:
            points.append({
                    "measurement": "restingHeartRate",
                    "time": datetime.fromisoformat(day['dateTime']),
                    "fields": {
                        "value": float(day['value']['restingHeartRate'])
                    }
                })

        if 'heartRateZones' in day['value']:
            for zone in day['value']['heartRateZones']:
                if 'caloriesOut' in zone and 'min' in zone and 'max' in zone and 'minutes' in zone:
                    points.append({
                            "measurement": "heartRateZones",
                            "time": datetime.fromisoformat(day['dateTime']),
                            "tags": {
                                "zone": zone['name']
                            },
                            "fields": {
                                "caloriesOut": float(zone['caloriesOut']),
                                "min": float(zone['min']),
                                "max": float(zone['max']),
                                "minutes": float(zone['minutes'])
                            }
                        })
                elif 'min' in zone and 'max' in zone and 'minutes' in zone:
                    points.append({
                            "measurement": "heartRateZones",
                            "time": datetime.fromisoformat(day['dateTime']),
                            "tags": {
                                "zone": zone['name']
                            },
                            "fields": {
                                "min": float(zone['min']),
                                "max": float(zone['max']),
                                "minutes": float(zone['minutes'])
                            }
                        })

    if 'activities-heart-intraday' in data:
        for value in data['activities-heart-intraday']['dataset']:
            time = datetime.fromisoformat(date + "T" + value['time'])
            utc_time = LOCAL_TIMEZONE.localize(time).astimezone(pytz.utc).isoformat()
            points.append({
                    "measurement": "heartrate",
                    "time": utc_time,
                    "fields": {
                        "value": float(value['value'])
                    }
                })

def process_levels(levels):
    for level in levels:
        type = level['level']
        if type == "asleep":
            type = "light"
        if type == "restless":
            type = "rem"
        if type == "awake":
            type = "wake"

        time = datetime.fromisoformat(level['dateTime'])
        utc_time = LOCAL_TIMEZONE.localize(time).astimezone(pytz.utc).isoformat()
        points.append({
                "measurement": "sleep_levels",
                "time": utc_time,
                "fields": {
                    "seconds": int(level['seconds'])
                }
            })

def fetch_activities(date):
    try:
        response = requests.get('https://api.fitbit.com/1/user/-/activities/list.json',
            headers={'Authorization': f'Bearer {FITBIT_ACCESS_TOKEN}', 'Accept-Language': FITBIT_LANGUAGE},
            params={'beforeDate': date, 'sort':'desc', 'limit':10, 'offset':0})
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        logging.error("HTTP request failed: %s", err)
        sys.exit(1)

    data = response.json()
    logging.info("Got activities from Fitbit")

    for activity in data['activities']:
        fields = {}

        if 'activeDuration' in activity:
            fields['activeDuration'] = int(activity['activeDuration'])
        if 'averageHeartRate' in activity:
            fields['averageHeartRate'] = int(activity['averageHeartRate'])
        if 'calories' in activity:
            fields['calories'] = int(activity['calories'])
        if 'duration' in activity:
            fields['duration'] = int(activity['duration'])
        if 'distance' in activity:
            fields['distance'] = float(activity['distance'])
            fields['distanceUnit'] = activity['distanceUnit']
        if 'pace' in activity:
            fields['pace'] = float(activity['pace'])
        if 'speed' in activity:
            fields['speed'] = float(activity['speed'])
        if 'elevationGain' in activity:
            fields['elevationGain'] = int(activity['elevationGain'])
        if 'steps' in activity:
            fields['steps'] = int(activity['steps'])

        for level in activity['activityLevel']:
            if level['name'] == 'sedentary':
                fields[level['name'] + "Minutes"] = int(level['minutes'])
            else:
                fields[level['name'] + "ActiveMinutes"] = int(level['minutes'])


        time = datetime.fromisoformat(activity['startTime'].strip("Z"))
        utc_time = time.astimezone(pytz.utc).isoformat()
        points.append({
            "measurement": "activity",
            "time": utc_time,
            "tags": {
                "activityName": activity['activityName']
            },
            "fields": fields
        })

connect(FITBIT_DATABASE)

if not FITBIT_ACCESS_TOKEN:
    if os.path.isfile('.fitbit-refreshtoken'):
        f = open(".fitbit-refreshtoken", "r")
        token = f.read()
        f.close()
        response = requests.post('https://api.fitbit.com/oauth2/token',
            data={
                "client_id": FITBIT_CLIENT_ID,
                "grant_type": "refresh_token",
                "redirect_uri": FITBIT_REDIRECT_URI,
                "refresh_token": token
            }, auth=(FITBIT_CLIENT_ID, FITBIT_CLIENT_SECRET))
    else:
        response = requests.post('https://api.fitbit.com/oauth2/token',
            data={
                "client_id": FITBIT_CLIENT_ID,
                "grant_type": "authorization_code",
                "redirect_uri": FITBIT_REDIRECT_URI,
                "code": FITBIT_INITIAL_CODE
            }, auth=(FITBIT_CLIENT_ID, FITBIT_CLIENT_SECRET))

    response.raise_for_status()

    json = response.json()
    FITBIT_ACCESS_TOKEN = json['access_token']
    refresh_token = json['refresh_token']
    f = open(".fitbit-refreshtoken", "w+")
    f.write(refresh_token)
    f.close()

try:
    response = requests.get('https://api.fitbit.com/1/user/-/devices.json', 
        headers={'Authorization': f'Bearer {FITBIT_ACCESS_TOKEN}', 'Accept-Language': FITBIT_LANGUAGE})
    response.raise_for_status()
except requests.exceptions.HTTPError as err:
    logging.error("HTTP request failed: %s", err)
    sys.exit(1)

data = response.json()
logging.info("Got devices from Fitbit")

for device in data:
    points.append({
        "measurement": "deviceBatteryLevel",
        "time": LOCAL_TIMEZONE.localize(datetime.fromisoformat(device['lastSyncTime'])).astimezone(pytz.utc).isoformat(),
        "tags": {
            "id": device['id'],
            "deviceVersion": device['deviceVersion'],
            "type": device['type'],
            "mac": device['mac'],
        },
        "fields": {
            "value": float(device['batteryLevel'])
        }
    })

# todo move sleep to function
def fetch_sleep():
    end = date.today()
    start = end - timedelta(days=30)

    try:
        response = requests.get(f'https://api.fitbit.com/1.2/user/-/sleep/date/{start.isoformat()}/{end.isoformat()}.json',
            headers={'Authorization': f'Bearer {FITBIT_ACCESS_TOKEN}', 'Accept-Language': FITBIT_LANGUAGE})
        response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        logging.error("HTTP request failed: %s", err)
        sys.exit(1)

    data = response.json()
    logging.info("Got sleep sessions from Fitbit")

    for day in data['sleep']:
        time = datetime.fromisoformat(day['startTime'])
        utc_time = LOCAL_TIMEZONE.localize(time).astimezone(pytz.utc).isoformat()
        if day['type'] == 'stages':
            points.append({
                "measurement": "sleep",
                "time": utc_time,
                "fields": {
                    "duration": int(day['duration']),
                    "efficiency": int(day['efficiency']),
                    "is_main_sleep": bool(day['isMainSleep']),
                    "minutes_asleep": int(day['minutesAsleep']),
                    "minutes_awake": int(day['minutesAwake']),
                    "time_in_bed": int(day['timeInBed']),
                    "minutes_deep": int(day['levels']['summary']['deep']['minutes']),
                    "minutes_light": int(day['levels']['summary']['light']['minutes']),
                    "minutes_rem": int(day['levels']['summary']['rem']['minutes']),
                    "minutes_wake": int(day['levels']['summary']['wake']['minutes']),
                }
            })
        else:
            points.append({
                "measurement": "sleep",
                "time": utc_time,
                "fields": {
                    "duration": int(day['duration']),
                    "efficiency": int(day['efficiency']),
                    "is_main_sleep": bool(day['isMainSleep']),
                    "minutes_asleep": int(day['minutesAsleep']),
                    "minutes_awake": int(day['minutesAwake']),
                    "time_in_bed": int(day['timeInBed']),
                    "minutes_deep": 0,
                    "minutes_light": int(day['levels']['summary']['asleep']['minutes']),
                    "minutes_rem": int(day['levels']['summary']['restless']['minutes']),
                    "minutes_wake": int(day['levels']['summary']['awake']['minutes']),
                }
            })
        
        if 'data' in day['levels']:
            process_levels(day['levels']['data'])
        
        if 'shortData' in day['levels']:
            process_levels(day['levels']['shortData'])

fetch_data('activities', 'steps')
fetch_data('activities', 'distance')
fetch_data('activities', 'floors')
fetch_data('activities', 'elevation')
fetch_data('activities', 'distance')
fetch_data('activities', 'minutesSedentary')
fetch_data('activities', 'minutesLightlyActive')
fetch_data('activities', 'minutesFairlyActive')
fetch_data('activities', 'minutesVeryActive')
fetch_data('activities', 'calories')
# fetch_data('activities', 'activityCalories') # todo this one times out when using 1y period...
fetch_data('body', 'weight')
fetch_data('body', 'fat')
fetch_data('body', 'bmi')
fetch_data('foods/log', 'water')
fetch_data('foods/log', 'caloriesIn')
fetch_heartrate(date.today())
fetch_activities((date.today() + timedelta(days=1)).isoformat())
fetch_sleep()


# todo: bug: it also writes values for future dates in the running year...
def initial_timeseries_import(start_year):
    global points

    for year in range(start_year, date.today().year + 1):
        logging.info(f'Importing {year}...')
        end_date = datetime(year, 12, 31)

        fetch_data('activities', 'steps', end_date)
        # fetch_data('activities', 'floors', end_date)
        fetch_data('activities', 'distance', end_date)
        # fetch_data('activities', 'elevation', end_date)
        fetch_data('activities', 'distance', end_date)
        fetch_data('activities', 'minutesSedentary', end_date)
        fetch_data('activities', 'minutesLightlyActive', end_date)
        fetch_data('activities', 'minutesFairlyActive', end_date)
        fetch_data('activities', 'minutesVeryActive', end_date)
        write_points(points)
        points = []
        fetch_data('activities', 'calories', end_date)
        # fetch_data('activities', 'activityCalories') # todo this one times out when using 1y period.., end_date.
        fetch_data('body', 'weight', end_date)
        fetch_data('body', 'fat', end_date)
        fetch_data('body', 'bmi', end_date)

        write_points(points)
        points = []

        for month in range(1, date.today().month + 1):
            fetch_heartrate(datetime(year, month, 1), intraday_api=False)
    
        write_points(points)
        points = []


initial_timeseries_import(2021)


# todo missing data
# - HRV
# - VO2 Max
# - breathing rate
# - SpO2
# - ... 