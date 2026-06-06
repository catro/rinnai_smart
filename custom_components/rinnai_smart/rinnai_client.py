import hashlib
import logging
import aiohttp
import backoff
import asyncio
import ssl
import json
import aiomqtt
import datetime
import re

logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger(__package__)

class HTTPClient:
    def __init__(self, username: str, password: str):
        self._username = username
        self._password = str.upper(hashlib.md5(password.encode("utf-8")).hexdigest())
        self._token = ""
        self._devices = []

    async def login(self) -> bool:
        params = {
            "username": self._username,
            "password": self._password,
            "accessKey": "A39C66706B83CCF0C0EE3CB23A39454D",
            "appType": "2",
            "appVersion": "3.1.0",
            "identityLevel": "0",
        }
        response = await self._get_url("/app/V1/login", params=params)
        if response.get("success") == False:
            LOGGER.error(f"Failed to login: {response}")
            return False

        LOGGER.info("Successfully logged in")
        self._token = response.get("data").get("token")
        return True

    async def _get_devices(self):
        headers = {"Authorization": f"Basic {self._token}"}
        return await self._get_url("/app/V1/device/list", headers=headers)

    async def get_devices(self) -> list[dict] | None:
        if self._token == "":
            await self.login()

        response = await self._get_devices()
        if response.get("success") == False:
            LOGGER.error(f"Failed to get devices: {response}")
            await self.login()
            response = await self._get_devices()
        devices = response.get("data", {}).get("list")

        devices_info = {}
        for device in devices:
            response = await self._get_device_information(device["id"])
            if response.get("success") == False:
                LOGGER.error(f"Failed to get device information: {response}")
                continue
            devices_info[device["id"]] = {
                "device": device,
                "info": response.get("data"),
            }

        return devices_info

    async def _get_device_information(self, device_id: str):
        headers = {"Authorization": f"Basic {self._token}"}
        params = {"deviceId": device_id}
        return await self._get_url(
            "/app/V1/device/processParameter", headers=headers, params=params
        )

    @backoff.on_exception(backoff.expo, aiohttp.ClientError, max_time=60)
    async def _get_url(self, url, **kwargs):
        async with aiohttp.ClientSession(
            base_url="https://iot.rinnai.com.cn", raise_for_status=True
        ) as session:
            async with session.get(url, **kwargs) as response:
                return await response.json()


class MQTTClient:
    def __init__(self, username: str, password: str, on_message):
        self._username = f"a:rinnai:SR:01:SR:{username}"
        self._password = str.upper(hashlib.md5(password.encode("utf-8")).hexdigest())
        self._on_message = on_message
        self._client = None

    async def run(self, ssl_context=None, subscribes=[]):
        if ssl_context is None:
            ssl_context = ssl.create_default_context()
        ts = datetime.datetime.now()
        try:
            async with aiomqtt.Client(
                "mqtt.rinnai.com.cn",
                8883,
                identifier=f"{self._username}:{ts.second}{ts.microsecond}",
                username=self._username,
                password=self._password,
                tls_context=ssl_context,
                tls_insecure=True,
            ) as client:
                LOGGER.info(f"MQTT connected")
                self._client = client
                for mac in subscribes:
                    await self.subscribe(mac)
                async for message in client.messages:
                    try:
                        await self._on_message(
                            message.topic.value, message.payload.decode("utf-8")
                        )
                    except Exception as e:
                        LOGGER.error(f"Error on message: {message.payload}")
        except aiomqtt.MqttError:
            self._client = None
        LOGGER.error("MQTT task exit")

    async def subscribe(self, mac):
        topic = f"rinnai/SR/01/SR/{mac}/+/"
        while self._client is None:
            await asyncio.sleep(1)
        await self._client.subscribe(topic)
        self._subscribed = True

    async def publish(self, topic: str, payload: str):
        while self._client is None:
            await asyncio.sleep(1)
        await self._client.publish(topic, payload)
        LOGGER.info(f"[Publish]: {payload}")


class RinnaiClient:
    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._http_client = HTTPClient(self._username, self._password)
        self._mqtt_client = MQTTClient(username, password, self._on_message)
        self._devices = {}
        self._subscribes = {}

    async def login(self) -> bool:
        return await self._http_client.login()

    async def get_devices(self) -> dict | None:
        self._devices = await self._http_client.get_devices()
        return self._devices

    import re

    async def _on_message(self, topic, payload):
        LOGGER.info(f"[RX]: {payload}")
        payload_str = None
        try:
            tokens = topic.split("/")
            if len(tokens) < 5:
                LOGGER.warning("Topic unknown")
                return
            mac = tokens[4]
            device_id = None
            for key, value in self._devices.items():
                if "device" in value and "mac" in value["device"] and value["device"]["mac"] == mac:
                    device_id = key
                    break
            if device_id is None:
                LOGGER.warning("Device ID not found")
                return
            info = self._devices[key]["info"]

            if isinstance(payload, bytes):
                payload_str = payload.decode('utf-8')
            else:
                payload_str = str(payload)
            payload_str = re.sub(r',\s*([}\]])', r'\1', payload_str)
            data = json.loads(payload_str)

            if data.get("ptn", "") != "J00":
                return
            if "enl" in data:
                for item in data["enl"]:
                    if "id" in item and "data" in item:
                        info[item["id"]] = item["data"]

            on_update, _ = self._subscribes.get(device_id)
            if on_update:
                await on_update(info)
        except UnicodeDecodeError as e:
            LOGGER.error(f"Error decoding message: {e}, original message: {payload}")
        except json.JSONDecodeError as e:
            LOGGER.error(f"Error parsing JSON: {e}, original message: {payload_str}")
        except Exception as e:
            LOGGER.error(
                f"Unexpected error in _on_message: {e}, original message: {payload_str if payload_str else payload}")

    async def run(self, ssl_context=None):
        BACKOFF_INIT = 10
        MAX_BACKOFF = 3600
        NEED_BACKOFF_SECONDS = datetime.timedelta(seconds=60)
        backoff = BACKOFF_INIT
        subscribes = []
        while True:
            now = datetime.datetime.now()
            try:
                LOGGER.info("Trying to connect to MQTT server...")
                await self._mqtt_client.run(ssl_context, subscribes)
            except Exception as e:
                LOGGER.error(f"MQTT connection error: {e}")
            if datetime.datetime.now() - now < NEED_BACKOFF_SECONDS:
                await self.get_devices()
                backoff = backoff << 1
                if backoff > MAX_BACKOFF:
                    backoff = MAX_BACKOFF
            else:
                backoff = BACKOFF_INIT
            LOGGER.warning(f"Reconnecting in {backoff} seconds")
            await asyncio.sleep(backoff)
            subscribes = []
            for _, (_, mac) in self._subscribes.items():
                subscribes.append(mac)

    async def subscribe(self, device_id: str, on_update):
        if device_id not in self._devices:
            LOGGER.error(f"Unknown device_id: {device_id}")
            return False
        mac = self._devices[device_id]["device"]["mac"]
        self._subscribes[device_id] = (on_update, mac)
        await on_update(self._devices[device_id]["info"])
        await self._mqtt_client.subscribe(mac)

    async def publish(self, device: dict, command_id, command_data):
        payload = {
            "code": device["authCode"],
            "id": device["deviceType"],
            "ptn": "J00",
            "enl": [{"id": command_id, "data": command_data}],
            "sum": "1",
        }
        mac = device["mac"]
        await self._mqtt_client.publish(f"rinnai/SR/01/SR/{mac}/set/", json.dumps(payload))


# define main entry for testing
async def main():
    async def on_message(topic: str, payload: str):
        await on_update(json.loads(payload))

    async def on_update(msg: str):
        print(f"Update: {json.dumps(msg)}")

    client = RinnaiClient("<USERNAME>", "<PASSWORD>")
    devices = await client.get_devices()
    task = asyncio.create_task(client.run())
    await client.subscribe(list(devices.keys())[0], on_update)

    # client = MQTTClient("<USERNAME>", "<PASSWORD>", on_message)
    # task = asyncio.create_task(client.run())
    # await client.subscribe("<MAC>")
    await asyncio.sleep(600)
    task.cancel()

    """
    login
    {"data":{"id":"<DEVICE_ID>","realName":"","nickName":"<USERNAME>","avatar":"","occupation":"","address":"","provinceId":"","cityId":"","districtId":"","token":"<TOKEN>","sex":"","identityLevel":0},"success":true}
    
    device_list
    {"data":{"list":[{"id":"6129881f47b2f22d89d53bca","mac":"<MAC>","name":"RUS-R**E86系列","sharePerson":"","share":true,"authCode":"<AUTH_CODE>","province":"","city":"","deviceType":"<DEVICE_TYPE>","productType":1,"classID":"<DEVICE_TYPE>","roomType":0,"parentMac":"","childMac":"","addParentTime":"","bindShare":false,"tempCtrlFirst":false,"barCode":"","modelType":"","model":"","operationMode":"C2","remark":"RUS-R**E86系列","serviceEndTime":"","errorCode":"0","remoteOnline":true,"filterEndTime":false,"projectId":"","projectName":"","userSelectedType":"<DEVICE_TYPE>","online":"1"}]},"success":true}

    processParameter
    {"data":{"bathWaterInjectionSetting":"0096","burningState":"1","childLock":"0","cycleModeSetting":"2","cycleReservationSetting":"1","cycleReservationTimeSetting":"00 00 00","errorCode":"0","faucetNotCloseSign":"1","hotWaterTempSetting":"20","hotWaterUseableSign":"0","lastCheckPoint":<xxx>,"operationMode":"C2","priority":"1","remainingWater":"0096","temporaryCycleInsulationSetting":"1","waterInjectionCompleteConfirm":"0","waterInjectionStatus":"0","userSelectedType":"<DEVICE_TYPE>","locations":"<xxx>","remark":"RUS-R**E86系列"},"success":true}

    mosquitto_sub -h mqtt.rinnai.com.cn -p 8883 -u "a:rinnai:SR:01:SR:<USERNAME>" -P "<PASSWOR_MD5>" --cafile /etc/ssl/certs/ca-certificates.crt -t "rinnai/SR/01/SR/<MAC>/set/"
    开机:     {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"power","data":"01"}],"sum":"1"}
    关机:     {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"power","data":"00"}],"sum":"1"}
    温度+:    {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"hotWaterTempOperate","data":"01"}],"sum":"1"}
    温度-:    {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"hotWaterTempOperate","data":"00"}],"sum":"1"}
    预约开:   {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"cycleReservationSetting","data":"01"}],"sum":"1"}
    预约关:   {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"cycleReservationSetting","data":"00"}],"sum":"1"}
    预约时间: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"cycleReservationTimeSetting","data":"00 00 00"}],"sum":"1"}
    循环开:   {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"temporaryCycleInsulationSetting","data":"01"}],"sum":"1"}
    循环关:   {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"temporaryCycleInsulationSetting","data":"00"}],"sum":"1"}
    标准循环: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"cycleModeSetting","data":"00"}],"sum":"1"}
    普通模式: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"regularMode","data":"01"}],"sum":"1"}
    低温模式: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"lowTempMode","data":"01"}],"sum":"1"}
    淋浴模式: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"showerMode","data":"01"}],"sum":"1"}
    水温按摩模式: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"massageMode","data":"01"}],"sum":"1"}
    厨房模式: {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"id":"kitchenMode","data":"01"}],"sum":"1"}
    "浴缸模式": {"code":"<AUTH_CODE>","id":"<DEVICE_TYPE>","ptn":"J00","enl":[{"data":"01","id":"bathModeConfirm"}],"sum":"1"}

    mosquitto_sub -h mqtt.rinnai.com.cn -p 8883 -u "a:rinnai:SR:01:SR:<USERNAME>" -P "<PASSWOR_MD5>" --cafile /etc/ssl/certs/ca-certificates.crt -t "rinnai/SR/01/SR/<MAC>/res/"
    {"ptn":"J00","code":"FFFF", "id":"<DEVICE_TYPE>","sum":"16", "enl":[ {"id":"errorCode","data":"0"}, {"id":"burningState","data":"1"}, {"id":"operationMode","data":"C2"}, {"id":"hotWaterTempSetting","data":"20"}, {"id":"bathWaterInjectionSetting","data":"0096"}, {"id":"waterInjectionStatus","data":"0"}, {"id":"remainingWater","data":"0096"}, {"id":"faucetNotCloseSign","data":"1"}, {"id":"hotWaterUseableSign","data":"0"}, {"id":"cycleModeSetting","data":"2"}, {"id":"cycleReservationTimeSetting","data":"00 00 00"}, {"id":"temporaryCycleInsulationSetting","data":"1"}, {"id":"cycleReservationSetting","data":"1"}, {"id":"waterInjectionCompleteConfirm","data":"0"}, {"id":"childLock","data":"0"}, {"id":"priority","data":"1"} ],"It":"<xxx>"}
    """


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
