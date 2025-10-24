import requests
import time

BASE_URL = "http://127.0.0.1:8000"

# 1. 获取页面元素
r = requests.post(f"{BASE_URL}/get_url_items", json={"url": "http://192.168.0.221:1680/dataMenuF"})
info = r.json()

input_id = info["inputs"][0]["id"]
button_id = info["buttons"][0]["id"]

# 2. 设置输入框的值
requests.post(f"{BASE_URL}/set_input_value", json={"input_id": input_id, "value": "其他能源"})
time.sleep(1)

# 3. 点击按钮
requests.post(f"{BASE_URL}/click_button", json={"button_id": button_id})
time.sleep(1)

# 4. 点击匹配标题
requests.post(f"{BASE_URL}/click_title_by_keyword", json={"keyword": "其他能源"})
time.sleep(1)

# 5. 提取表格
r = requests.post(f"{BASE_URL}/extract_table")
data = r.json()
print(data)