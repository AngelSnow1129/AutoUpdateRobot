# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import time
import random
import requests
import logging
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------- 配置 --------------------
RESP_FILE = 'resp.txt'           # 存放API返回的原始数据
URL_FILE = 'url.txt'             # 存放去重后的URL列表
WEB_SOURCE_URL = "http://www.hunanie.com/col.jsp?id=123"  # 网页抓取 URL
DOWNLOAD_FOLDER = 'download'
SOURCE_DIRECTORY = DOWNLOAD_FOLDER
DESTINATION_DIRECTORY = DOWNLOAD_FOLDER

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36"
}

CHINESE_NUMERALS = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}

logging.basicConfig(level=logging.INFO, filename='download_log.log', filemode='w',
                    format='%(asctime)s - %(levelname)s - %(message)s')

CATEGORY_KEYWORDS = {
    "机器人技术": ["机器人"],
    "三维创意设计": ["三维创意设计", "三维设计"],
    "无人机技术": ["无人机"],
    "电子技术": ["电子技术", "电子"],
    "C语言": ["C语言"],
    "Python": ["python", "Python"],
    "图形化": ["图形化"]
}

# -------------------- 辅助函数 --------------------
def extract_urls_from_web(html_content):
    """从网页 HTML 中提取 URL"""
    soup = BeautifulSoup(html_content, 'html.parser')
    urls = []
    for link in soup.find_all('a', href=True):
        href = link['href'].strip()
        if href.startswith("http://www.hunanie.com/nd.jsp"):
            urls.append(href)
    return urls

# -------------------- 第一步：提取 URL 并去重 --------------------
def process_urls():
    existing_urls = set()
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as existing_file:
            existing_urls.update(line.strip() for line in existing_file if line.strip())
    except FileNotFoundError:
        pass

    if os.path.exists(RESP_FILE):
        with open(RESP_FILE, 'r', encoding='utf-8') as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    for item in data.get('list', []):
                        url = item.get('url', '')
                        if url.startswith('http://www.hunanie.com/nd.jsp') and url not in existing_urls:
                            with open(URL_FILE, 'a', encoding='utf-8') as output_file:
                                output_file.write(url + '\n')
                            existing_urls.add(url)
                except json.JSONDecodeError as e:
                    logging.error(f"无法解析 JSON 的行: {line}, 错误: {e}")
    else:
        logging.warning(f"{RESP_FILE} 不存在，跳过 JSON 提取。")

    try:
        response = requests.get(WEB_SOURCE_URL, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            urls_from_web = extract_urls_from_web(response.text)
            with open(URL_FILE, 'a', encoding='utf-8') as f:
                for url in urls_from_web:
                    if url not in existing_urls:
                        f.write(url + '\n')
                        existing_urls.add(url)
        else:
            logging.error(f"网页抓取失败，状态码: {response.status_code}")
    except requests.RequestException as e:
        logging.error(f"网页抓取异常: {e}")

    if not existing_urls:
        with open(URL_FILE, 'w', encoding='utf-8') as f:
            f.write("# 没有可用的下载链接\n")

# -------------------- 第二步：并发下载文件 --------------------
def fetch_and_download(url):
    """访问页面并下载里面的附件"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
    except requests.RequestException as e:
        logging.error(f"访问 {url} 失败: {e}")
        return

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        download_link_elements = soup.find_all('a', {'class': 'news_detail_download_item_link'})
        for elem in download_link_elements:
            download_link = elem.get('href', '')
            if not download_link.startswith('http'):
                download_link = 'http:' + download_link
            file_name = elem.find('span', {'class': 'news_detail_download_item_text'}).text.strip()

            logging.info("下载链接: %s 名字: %s", download_link, file_name)

            if not os.path.exists(DOWNLOAD_FOLDER):
                os.makedirs(DOWNLOAD_FOLDER)

            file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
            try:
                file_response = requests.get(download_link, timeout=15)
                with open(file_path, 'wb') as f:
                    f.write(file_response.content)
            except requests.RequestException as e:
                logging.error(f"下载 {download_link} 失败: {e}")
    else:
        logging.error("访问 %s 失败，状态码: %s", url, response.status_code)

def download_files_concurrent():
    if not os.path.exists(URL_FILE):
        logging.warning(f"{URL_FILE} 不存在，跳过下载。")
        return

    with open(URL_FILE, 'r', encoding='utf-8') as file:
        urls = [line.strip() for line in file if line.strip() and not line.startswith("#")]

    if not urls:
        logging.warning("URL 文件为空，跳过下载任务。")
        return

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_and_download, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                future.result()
                time.sleep(random.uniform(0.5, 1.5))
            except Exception as e:
                logging.error(f"下载任务出错 {url}: {e}")

# -------------------- 第三步：按类型和等级分类文件 --------------------
def convert_chinese_to_arabic(chinese_number):
    return CHINESE_NUMERALS.get(chinese_number, -1)

def classify_and_copy_files(source_directory, destination_directory):
    for root, dirs, files in os.walk(source_directory):
        for file in files:
            # 分类
            category = "其他"
            for cat_name, keywords in CATEGORY_KEYWORDS.items():
                if any(kw.lower() in file.lower() for kw in keywords):
                    category = cat_name
                    break

            # 等级
            match = re.search(r'([一二三四五六七八九\d]+)级', file)
            if match:
                level_str = match.group(1)
                if level_str in CHINESE_NUMERALS:
                    level = convert_chinese_to_arabic(level_str)
                else:
                    try:
                        level = int(level_str)
                    except ValueError:
                        level = "其他"
            else:
                level = "其他"

            dest_path = os.path.join(destination_directory, category, f"等级{level}" if level != "其他" else level)
            os.makedirs(dest_path, exist_ok=True)
            shutil.copy(os.path.join(root, file), dest_path)

# -------------------- 主流程 --------------------
if __name__ == "__main__":
    logging.info("=== 开始提取 URL ===")
    process_urls()

    logging.info("=== 开始并发下载文件（5线程） ===")
    download_files_concurrent()

    logging.info("=== 开始分类文件（按类型+等级） ===")
    classify_and_copy_files(SOURCE_DIRECTORY, DESTINATION_DIRECTORY)

    logging.info("=== 任务完成 ===")
