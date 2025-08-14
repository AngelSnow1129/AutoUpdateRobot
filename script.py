import json
import os
import time
import random
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from urllib.parse import quote, urljoin

# --- 配置 ---
BASE_URL = "http://www.hunanie.com/nr.jsp"
SITE_DOMAIN = "http://www.hunanie.com"
API_ENDPOINT_CONTAINS = "/rajax/news_h.jsp"
# [修正] 目录名与 a​​ction 保持一致
DOWNLOAD_DIR = "downloads_final"
# [新增] 定义历史记录文件名，与 action 保持一致
PROCESSED_URLS_FILE = "processed_urls.txt"

# 完整的分组ID映射关系
GROUP_ID_MAP = {
    14: "真题：机器人技术",
    15: "真题：软件编程（图形化）",
    16: "真题：软件编程（Python）",
    17: "真题：软件编程（C语言）",
    18: "真题：三维创意设计",
    19: "考试标准",
    4:  "等级考试",
}

# 存储所有捕获到的数据（包括HTML抓取和API响应）
all_data_container = []

# --- [新增] 历史记录处理函数 ---
def load_processed_urls(filename):
    """从文件加载已处理的URL集合"""
    if not os.path.exists(filename):
        return set()
    with open(filename, 'r', encoding='utf-8') as f:
        # 确保读取时去除空行和多余空格
        return set(line.strip() for line in f if line.strip())

def save_processed_urls(filename, urls):
    """将已处理的URL集合保存到文件"""
    with open(filename, 'w', encoding='utf-8') as f:
        for url in sorted(list(urls)):
            f.write(url + '\n')

def generate_category_urls(base_url, id_map):
    """根据分组ID生成所有分类页面的起始URL"""
    urls = {}
    for group_id, name in id_map.items():
        req_args = {"args": {"groupId": group_id, "jpt": 4}, "type": 32}
        encoded_args = quote(json.dumps(req_args, separators=(',', ':')))
        urls[name] = f"{base_url}?_reqArgs={encoded_args}"
    return urls

def handle_response(response):
    """Playwright的网络响应处理器，用于捕获第2页及以后的JSON数据"""
    if API_ENDPOINT_CONTAINS in response.url and response.ok:
        try:
            print(f"[*] 成功捕获API响应 (分页数据): {response.url}")
            data = response.json()
            all_data_container.append(data)
        except Exception as e:
            print(f"[!] 解析JSON响应失败: {e}")

def scrape_initial_page_data(page):
    """[优化] 直接从HTML中解析第一页的文章列表"""
    print("  -> 正在从HTML中直接解析第一页的数据...")
    articles = []
    try:
        page.wait_for_selector('.news_result_item_line', timeout=10000)
        entries = page.locator('.news_result_item_line').all()
        
        for entry in entries:
            title_loc = entry.locator('.news_result_item_title')
            date_loc = entry.locator('.news_result_item_date')
            link_loc = entry.locator('a.news_result_item_link')
            
            title = title_loc.inner_text().strip() if title_loc.count() > 0 else ""
            date_str = date_loc.inner_text().strip() if date_loc.count() > 0 else ""
            relative_url = link_loc.get_attribute('href') if link_loc.count() > 0 else ""

            if title and relative_url:
                # [优化] 将相对URL转换为绝对URL
                absolute_url = urljoin(SITE_DOMAIN, relative_url)
                articles.append({
                    "title": title,
                    "dateStr": date_str,
                    "url": absolute_url
                })
        
        print(f"    -> 成功解析到 {len(articles)} 篇文章。")
        
        if articles:
            # 模仿API响应的结构，以便后续统一处理
            return {"success": True, "list": articles, "source": "HTML_Scrape"}
            
    except PlaywrightTimeoutError:
        print("  -> 在初始页面未找到文章列表。")
    except Exception as e:
        print(f"  -> 解析初始页面时出错: {e}")
    return None

def collect_data_with_playwright(category_urls):
    """步骤1: 使用Playwright遍历所有分类，抓取数据"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        page = context.new_page()
        
        page.on("response", handle_response)

        for category_name, url in category_urls.items():
            print(f"\n{'='*20}\n正在处理分类: {category_name}\n{'='*20}")
            try:
                page.goto(url, wait_until='networkidle', timeout=60000)
                
                initial_data = scrape_initial_page_data(page)
                if initial_data:
                    all_data_container.append(initial_data)

                page_num = 1
                while True:
                    next_button = page.locator('.pagination_btn_next')
                    
                    if not next_button.is_visible() or next_button.is_disabled():
                        print(f"分类 '{category_name}' 已到达最后一页。")
                        break
                    
                    page_num += 1
                    print(f"  -> 正在点击加载第 {page_num} 页...")
                    next_button.click()
                    page.wait_for_load_state('networkidle', timeout=30000)
                    time.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"处理分类 '{category_name}' 时发生错误: {e}")
        
        browser.close()

def parse_and_download(data_list, processed_urls):
    """步骤2: 解析数据，过滤已处理的URL，并下载新文件"""
    new_urls_to_process = set()
    print("\n--- 正在从所有数据中解析文章详情页URL ---")
    for data in data_list:
        article_list = data.get('list') or data.get('data', {}).get('newsList', [])
        for article in article_list:
            if article.get('url'):
                full_url = article['url']
                # [核心逻辑] 如果URL未被处理过，则加入待处理集合
                if full_url not in processed_urls:
                    new_urls_to_process.add(full_url)
    
    total_found = len(new_urls_to_process) + len(processed_urls)
    print(f"解析完成，共找到 {total_found} 个不重复的文章页面。")
    print(f"历史记录中已有 {len(processed_urls)} 个，本次新增 {len(new_urls_to_process)} 个待处理页面。")

    if not new_urls_to_process:
        print("没有新的文章页面需要处理。")
        return processed_urls

    # [优化] 确保目录存在
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    print(f"\n--- 开始从 {len(new_urls_to_process)} 个新页面中批量下载文件 ---")
    newly_processed_urls = set()
    for i, url in enumerate(sorted(list(new_urls_to_process))):
        print(f"\n处理新页面 {i+1}/{len(new_urls_to_process)}: {url}")
        try:
            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            download_elements = soup.select('a.news_detail_download_item_link')
            
            if not download_elements:
                print("  -> 未找到下载链接。")
            else:
                for element in download_elements:
                    link = element.get('href')
                    name_tag = element.select_one('span.news_detail_download_item_text')
                    if not link or not name_tag:
                        continue
                    
                    file_name = name_tag.text.strip().replace("/", "-") # 替换非法字符
                    file_path = os.path.join(DOWNLOAD_DIR, file_name)

                    if os.path.exists(file_path):
                        print(f"  -> 文件已存在, 跳过: {file_name}")
                        continue
                    
                    download_url = urljoin(SITE_DOMAIN, link)
                    
                    print(f"  -> 正在下载: {file_name}")
                    file_response = requests.get(download_url, timeout=90, stream=True)
                    file_response.raise_for_status()
                    with open(file_path, 'wb') as f:
                        for chunk in file_response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print(f"    下载成功!")
                    time.sleep(random.uniform(1, 2))

            # 无论页面有无附件，只要成功访问，就标记为已处理
            newly_processed_urls.add(url)
            
        except requests.RequestException as e:
            print(f"  下载页面或文件时出错: {e}")
        except Exception as e:
            print(f"  处理页面时发生未知错误: {e}")

    # 返回旧记录和新记录的并集
    return processed_urls.union(newly_processed_urls)

if __name__ == "__main__":
    # [新增] 加载历史记录
    processed_urls_history = load_processed_urls(PROCESSED_URLS_FILE)
    
    category_links = generate_category_urls(BASE_URL, GROUP_ID_MAP)
    
    collect_data_with_playwright(category_links)
    
    if all_data_container:
        with open("captured_all_data.json", "w", encoding="utf-8") as f:
            json.dump(all_data_container, f, ensure_ascii=False, indent=2)
        print(f"\n所有 {len(all_data_container)} 份数据已备份到 captured_all_data.json")
    
    updated_processed_urls = parse_and_download(all_data_container, processed_urls_history)
    
    # [新增] 如果有新处理的URL，则保存更新后的历史记录
    if len(updated_processed_urls) > len(processed_urls_history):
        print(f"\n更新URL历史记录文件: {PROCESSED_URLS_FILE}")
        save_processed_urls(PROCESSED_URLS_FILE, updated_processed_urls)
    
    print("\n所有任务已完成。")