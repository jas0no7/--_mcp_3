# -*- coding: utf-8 -*-
from fastapi import FastAPI, Body
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import asyncio, time, io, sys
import anyio
import ddddocr
anyio.lowlevel.RUN_SYNC_IN_WORKER_THREAD = False
# ------------------ Windows 异步兼容 ------------------
asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI(title="Playwright Page Interaction")

p, browser, context, page = None, None, None, None


# --------------------------------------------------------
# 初始化浏览器
# --------------------------------------------------------
LOGIN_URL = "http://192.168.0.221:1680/dataMenuF"

def _perform_login_with_ocr(cur_page):
    """
    打开登录页，识别验证码并登录，然后点击“数据目录”。
    依据用户提供脚本，仅需识别验证码并提交。
    """
    # 打开登录页
    cur_page.goto(LOGIN_URL)

    # 等待验证码加载并截图为内存字节
    xpath_img = '//*[@id="app"]/div/div[1]/form/div[3]/div/div[2]/img'
    cur_page.wait_for_selector(f"xpath={xpath_img}", timeout=10000)
    img_element = cur_page.locator(f"xpath={xpath_img}")
    img_bytes = img_element.screenshot()

    # 初始化 ddddocr，屏蔽其广告输出
    _stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        ocr = ddddocr.DdddOcr()
    finally:
        sys.stdout = _stdout

    # 识别验证码
    res = ocr.classification(img_bytes)

    # 填入验证码并提交登录
    cur_page.fill('input[placeholder="验证码："]', res)
    cur_page.click('xpath=//*[@id="app"]/div/div[1]/form/div[4]/div/button/span/span')

    # 等待进入首页并点击“数据目录”
    cur_page.wait_for_selector("text=数据目录", timeout=15000)
    cur_page.click("text=数据目录")
    cur_page.wait_for_load_state("networkidle")


def start_browser(url: str):
    global p, browser, context, page
    if page:
        try:
            page.goto(url, wait_until="networkidle")
        except Exception:
            pass
        return page
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    try:
        _perform_login_with_ocr(page)
    except Exception:
        pass
    try:
        page.goto(url, wait_until="networkidle")
    except Exception:
        pass
    return page


def stop_browser():
    global p, browser, context, page
    try:
        if browser:
            browser.close()
    except Exception:
        pass
    try:
        if p:
            p.stop()
    except Exception:
        pass
    p = None
    browser = None
    context = None
    page = None


# --------------------------------------------------------
# 获取页面元素接口
# --------------------------------------------------------
class UrlParam(BaseModel):
    url: str


@app.post("/get_url_items")
def get_url_items(params: UrlParam):
    """
    返回某一个页面的所有元素：按钮、输入框、表格等
    """
    global page
    page = start_browser(params.url)

    result = {"buttons": [], "inputs": [], "tables": []}

    # 按钮
    buttons = page.locator("button, input[type='button'], input[type='submit']")
    for i in range(buttons.count()):
        el = buttons.nth(i)
        name = el.inner_text().strip() or el.get_attribute("value") or f"按钮{i+1}"
        result["buttons"].append({"id": f"btn_{i+1}", "name": name})

    # 输入框
    inputs = page.locator("input:not([type='button']):not([type='submit']), textarea, select")
    for i in range(inputs.count()):
        el = inputs.nth(i)
        name = el.get_attribute("placeholder") or el.get_attribute("name") or f"输入框{i+1}"
        result["inputs"].append({"id": f"input_{i+1}", "name": name})

    # 表格
    tables = page.locator("table")
    result["tables"] = [f"table_{i+1}" for i in range(tables.count())]

    return result


# --------------------------------------------------------
# 点击按钮接口
# --------------------------------------------------------
class ClickParam(BaseModel):
    button_id: str


@app.post("/click_button_and_page_items")
def click_button_and_page_items(params: ClickParam):
    """
    点击指定按钮（如搜索），并返回新页面上所有 h3 标签信息。
    返回格式：{"page_items": [{"text": "...", "href": "..."}]}
    """
    global page
    if not page:
        return {"error": "页面未打开"}
    try:
        buttons = page.locator("button, input[type='button'], input[type='submit']")
        index = int(params.button_id.split("_")[-1]) - 1
        el = buttons.nth(index)
        name = el.inner_text().strip() or el.get_attribute("value")

        # 滚动以确保可点击
        try:
            el.scroll_into_view_if_needed()
        except Exception:
            pass

        # 点击一次
        el.click()

        # 若点击产生新标签页，则切换
        new_pg = None
        try:
            new_pg = context.wait_for_event("page", timeout=5000)
        except Exception:
            new_pg = None
        if new_pg is not None:
            try:
                new_pg.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            page = new_pg
        else:
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        # 等待页面加载 h3 元素
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if page.locator("h3").count() > 0:
                    break
            except Exception:
                pass
            time.sleep(0.3)

        # 收集所有 h3 标签信息
        def collect_h3(from_page):
            items_acc = []
            try:
                for elh in from_page.query_selector_all("h3"):
                    text_val = (elh.inner_text() or "").strip()
                    href_val = ""
                    a_el = elh.query_selector("a")
                    if a_el:
                        href_val = a_el.get_attribute("href") or ""
                    items_acc.append({"text": text_val, "href": href_val})
            except Exception:
                pass
            # 子 frame
            try:
                for fr in from_page.frames:
                    for elh in fr.query_selector_all("h3"):
                        text_val = (elh.inner_text() or "").strip()
                        href_val = ""
                        a_el = elh.query_selector("a")
                        if a_el:
                            href_val = a_el.get_attribute("href") or ""
                        items_acc.append({"text": text_val, "href": href_val})
            except Exception:
                pass
            return items_acc

        names = [i["text"] for i in collect_h3(page)]
        h3_items = [{"id": f"h3_{i+1}", "name": n} for i, n in enumerate(names)]
        return {"page_items": {"links": h3_items}}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------
# 设置输入框值接口
# --------------------------------------------------------
class InputParam(BaseModel):
    input_id: str
    value: str


@app.post("/set_input_value")
def set_input_value(params: InputParam):
    """
    设置输入框或选择框的值
    """
    global page
    if not page:
        return {"error": "页面未打开"}
    try:
        inputs = page.locator("input:not([type='button']):not([type='submit']), textarea, select")
        index = int(params.input_id.split("_")[-1]) - 1
        el = inputs.nth(index)
        el.fill(params.value)
        return {"status": "200", "info": f"已设置 {params.input_id} 的值为 {params.value}"}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------
# 点击标题接口（新版）
# --------------------------------------------------------
class TitleParam(BaseModel):
    link_id: str


@app.post("/click_link_and_page_items")
def click_title_by_keyword(params: TitleParam):
    """
    点击页面中指定 id（如 h3_1）的标题，并抓取表格数据
    """
    global page
    if not page:
        return {"error": "页面未打开"}
    try:
        idx = int(str(params.link_id).split("_")[-1]) - 1
        if idx < 0:
            return {"error": "id 序号无效"}

        # 收集 h3 元素（包含子 frame）
        elements = []
        elements.extend(page.query_selector_all("h3"))
        for fr in page.frames:
            elements.extend(fr.query_selector_all("h3"))

        if idx >= len(elements):
            return {"error": "未找到对应的 h3"}

        target = elements[idx]
        target.scroll_into_view_if_needed()
        target.click()
        time.sleep(1)

        # 抓取表格
        page.wait_for_selector("table")
        headers = [th.inner_text().strip() for th in page.query_selector_all("table thead tr th")]
        rows = page.query_selector_all("table tbody tr")
        data = []
        for row in rows:
            cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
            if headers and len(cells) == len(headers):
                data.append(dict(zip(headers, cells)))
            elif cells:
                data.append({f"col_{i+1}": v for i, v in enumerate(cells)})

        result = {
            "table": [
                {
                    "table_name": "",
                    "page_size": len(data),
                    "page_count": "",
                    "buttons": [{"id": "p1216", "name": "前往"}],
                    "data": data
                }
            ]
        }

        # 获取分页信息
        try:
            pc_el = page.locator('xpath=//*[@id="app"]/div/section/div/div/div[1]/div/div[3]/div[2]/div[2]/div[2]/div/span[1]')
            if pc_el.count() > 0:
                page_count_text = (pc_el.nth(0).inner_text() or "").strip()
                result["table"][0]["page_count"] = page_count_text
        except Exception:
            pass

        # 获取分页按钮 id
        try:
            btn_el = page.locator('xpath=//*[@id="app"]/div/section/div/div/div[1]/div/div[3]/div[2]/div[2]/div[2]/div/span[3]/span[1]')
            if btn_el.count() > 0:
                btn_id = btn_el.nth(0).get_attribute("id") or "p1216"
                result["table"][0]["buttons"] = [{"id": btn_id, "name": "前往"}]
        except Exception:
            pass

        stop_browser()
        return result
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------
# 抽取表格接口（旧版）
# --------------------------------------------------------
def extract_table_deprecated():
    """
    抽取当前页面的表格数据并返回 JSON
    """
    global page
    if not page:
        return {"error": "页面未打开"}
    try:
        page.wait_for_selector("table")
        headers = [th.inner_text().strip() for th in page.query_selector_all("table thead tr th")]
        rows = page.query_selector_all("table tbody tr")
        data = []
        for row in rows:
            cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
            if headers and len(cells) == len(headers):
                data.append(dict(zip(headers, cells)))
            elif cells:
                data.append({f"col_{i+1}": v for i, v in enumerate(cells)})
        result = {"rows": len(data), "data": data}
        stop_browser()
        return result
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------
# 健康检测接口
# --------------------------------------------------------
@app.get("/ping")
def ping():
    return {"status": "ok"}
