from fastapi import FastAPI, Body
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import asyncio, os, json, time
import anyio
anyio.lowlevel.RUN_SYNC_IN_WORKER_THREAD = False
# ------------------ Windows 异步兼容 ------------------
asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI(title="Playwright Page Interaction")

STATE_PATH = "storageState.json"
p, browser, context, page = None, None, None, None


# --------------------------------------------------------
# 初始化浏览器
# --------------------------------------------------------
def _is_login_required(cur_page):
    try:
        current_url = (cur_page.url or "").lower()
    except Exception:
        current_url = ""
    if any(key in current_url for key in ["login", "signin", "auth"]):
        return True
    try:
        if cur_page.locator("input[type='password']").count() > 0:
            return True
        if cur_page.locator("img[src*='captcha' i], img[alt*='captcha' i], input[name*='captcha' i], input[id*='captcha' i]").count() > 0:
            return True
    except Exception:
        pass
    return False


def _ensure_login_and_refresh_state(target_url: str, wait_seconds: int = 180):
    global context, page
    try:
        if _is_login_required(page):
            deadline = time.time() + max(1, wait_seconds)
            while time.time() < deadline:
                if not _is_login_required(page):
                    break
                time.sleep(1)
            if not _is_login_required(page):
                try:
                    context.storage_state(path=STATE_PATH)
                except Exception:
                    pass
                try:
                    if page.url != target_url:
                        page.goto(target_url, wait_until="networkidle")
                except Exception:
                    pass
    except Exception:
        # 保守处理，任何异常都不阻断原有浏览流程
        pass


def start_browser(url: str):
    global p, browser, context, page
    if page:
        try:
            page.goto(url, wait_until="networkidle")
        except Exception:
            pass
        _ensure_login_and_refresh_state(url)
        return page
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state=STATE_PATH if os.path.exists(STATE_PATH) else None)
    page = context.new_page()
    page.goto(url, wait_until="networkidle")
    _ensure_login_and_refresh_state(url)
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


@app.post("/click_link_and_page_items")
def click_link_and_page_items(params: ClickParam):
    """
    点击指定按钮（如搜索），并返回新页面上所有 h3 标签信息
    返回格式：{"page_items": [{"text": "...", "href": "..."}, ...]}
    """
    global page
    if not page:
        return {"error": "页面未打开"}
    try:
        buttons = page.locator("button, input[type='button'], input[type='submit']")
        index = int(params.button_id.split("_")[-1]) - 1
        el = buttons.nth(index)
        name = el.inner_text().strip() or el.get_attribute("value")
        # 滚动确保可点击
        try:
            el.scroll_into_view_if_needed()
        except Exception:
            pass

        # 点击一次
        el.click()

        # 若点击产生新标签页，则切换到新页面
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
            # 切换当前全局 page 到新页面
            page = new_pg
        else:
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        # 最多等待一会儿，直到出现 h3
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if page.locator("h3").count() > 0:
                    break
            except Exception:
                pass
            time.sleep(0.3)

        # 收集所有 frame 中的 h3 标签信息
        def collect_h3(from_page):
            items_acc = []
            # 顶层
            try:
                for elh in from_page.query_selector_all("h3"):
                    try:
                        text_val = (elh.inner_text() or "").strip()
                    except Exception:
                        text_val = ""
                    href_val = ""
                    try:
                        a_el = elh.query_selector("a")
                        if a_el:
                            href_val = a_el.get_attribute("href") or ""
                    except Exception:
                        pass
                    items_acc.append({"text": text_val, "href": href_val})
            except Exception:
                pass
            # 子 frame
            try:
                for fr in from_page.frames:
                    try:
                        for elh in fr.query_selector_all("h3"):
                            try:
                                text_val = (elh.inner_text() or "").strip()
                            except Exception:
                                text_val = ""
                            href_val = ""
                            try:
                                a_el = elh.query_selector("a")
                                if a_el:
                                    href_val = a_el.get_attribute("href") or ""
                            except Exception:
                                pass
                            items_acc.append({"text": text_val, "href": href_val})
                    except Exception:
                        pass
            except Exception:
                pass
            return items_acc

        # 基于页面与子 frame 收集所有 h3 文本，转为 id/name 形式
        def collect_h3_names(from_page):
            items_acc = []
            try:
                for elh in from_page.query_selector_all("h3"):
                    try:
                        text_val = (elh.inner_text() or "").strip()
                    except Exception:
                        text_val = ""
                    items_acc.append(text_val)
            except Exception:
                pass
            try:
                for fr in from_page.frames:
                    try:
                        for elh in fr.query_selector_all("h3"):
                            try:
                                text_val = (elh.inner_text() or "").strip()
                            except Exception:
                                text_val = ""
                            items_acc.append(text_val)
                    except Exception:
                        pass
            except Exception:
                pass
            return items_acc

        names = collect_h3_names(page)
        h3_items = [{"id": f"h3_{i+1}", "name": n} for i, n in enumerate(names)]
        return {"page_items": {"h3": h3_items}}
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
        return {"status": "200", "info": "设置按钮值成功"}
        return {"status": "200", "info": "设置按钮值成功"}
        return {"status": f"已设置 {params.input_id} 的值为 {params.value}"}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------
# 点击标题接口（新增）
# --------------------------------------------------------
class TitleParam(BaseModel):
    id: str


@app.post("/click_title_by_keyword")
def click_title_by_keyword(params: TitleParam):
    """
    点击页面上包含指定关键字的标题（h3.textOF1）
    """
    global page
    if not page:
        return {"error": "页面未打开"}
    # 基于 id（如 h3_2）点击对应 h3，保持与上个接口相同的顺序规则
    try:
        # 解析 id
        try:
            idx = int(str(params.id).split("_")[-1]) - 1
        except Exception:
            return {"error": "id 格式错误，应形如 h3_1"}
        if idx < 0:
            return {"error": "id 序号无效"}

        # 收集元素：先顶层 h3，再各子 frame 的 h3（与 click_link_and_page_items 中顺序一致）
        elements = []
        try:
            elements.extend(page.query_selector_all("h3"))
        except Exception:
            pass
        try:
            for fr in page.frames:
                try:
                    elements.extend(fr.query_selector_all("h3"))
                except Exception:
                    pass
        except Exception:
            pass

        if idx >= len(elements):
            return {"error": "未找到对应的 h3"}

        target = elements[idx]
        try:
            target.scroll_into_view_if_needed()
        except Exception:
            pass
        target.click()
        time.sleep(1)
        return {"status": "ok", "clicked": params.id}
    except Exception as e:
        return {"error": str(e)}
    # 下面为兼容旧实现的遗留代码（按关键字），已不会被执行
    try:
        elements = page.locator("h3.textOF1")
        found = False
        for i in range(elements.count()):
            el = elements.nth(i)
            text = el.inner_text().strip()
            if params.keyword in text:
                el.scroll_into_view_if_needed()
                el.click()
                found = True
                break
        if not found:
            return {"error": f"未找到包含 {params.keyword} 的标题"}
        time.sleep(3)
        return {"status": f"已点击标题: {params.keyword}"}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------
# 提取表格接口（新增）
# --------------------------------------------------------
@app.post("/extract_table")
def extract_table():
    """
    提取当前页面的表格数据并返回 JSON
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
        # 成功提取后自动关闭浏览器，清理会话
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
