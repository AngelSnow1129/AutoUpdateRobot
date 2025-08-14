import requests
from bs4 import BeautifulSoup
import os
import time
import random
import json
import logging
import shutil
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ==============================================================================
# --- 日志配置 ---
# ==============================================================================
# 确保日志文件和脚本在同一目录下
log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'download_log.log') if '__file__' in locals() else 'download_log.log'
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 避免在多次导入时重复添加handler
if not logger.handlers:
    # 文件处理器
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    # 统一格式化
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    # 添加到logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# ==============================================================================
# --- 全局配置 ---
# ==============================================================================
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36"}
HISTORY_URL_FILE = 'processed_urls.txt'
DOWNLOAD_DIR_TEMP = 'downloads_temp'  # 所有文件先下载到这里
DOWNLOAD_DIR_CLASSIFIED = 'downloads_final' # 最终分类归档目录
MAX_THREADS = 5  # 并发下载线程数

# 用于分类的关键字和映射 (关键字建议用小写)
SUBJECT_KEYWORDS = {
    "python": "Python",
    "c语言": "C语言",
    "图形化": "图形化编程",
    "机器人": "机器人",
    "三维创意设计": "三维创意设计",
    "无人机": "无人机",
    "电子技术": "电子技术",
}
CHINESE_NUMERALS = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9'}

# ==============================================================================
# --- 核心功能函数 ---
# ==============================================================================

def get_urls_from_sources():
    """从所有已知来源获取URL列表"""
    logger.info("开始从所有来源获取URL...")
    urls_from_robot = get_urls_from_robot()
    # 在这里可以添加其他获取URL的函数
    # urls_from_other_source = get_urls_from_other_source()
    all_urls = set(urls_from_robot) # | set(urls_from_other_source)
    logger.info(f"所有来源共找到 {len(all_urls)} 个不重复的详情页URL。")
    return list(all_urls)

def get_urls_from_robot():
    """从主页面的文章列表中提取所有详情页URL"""
    try:
        url = "http://www.hunanie.com/col.jsp?id=123"
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        # 查找所有包含 nd.jsp 的链接
        urls = [link['href'] for link in soup.find_all('a', href=True) if "http://www.hunanie.com/nd.jsp" in link.get('href', '')]
        logger.info(f"成功从robot页面提取{len(set(urls))}个URL")
        return urls
    except requests.RequestException as e:
        logger.error(f"提取robot页面URL时出错: {e}")
        return []

def load_processed_urls():
    """加载已处理过的URL历史记录"""
    if not os.path.exists(HISTORY_URL_FILE):
        return set()
    try:
        with open(HISTORY_URL_FILE, 'r', encoding='utf-8') as f:
            return {line.strip() for line in f if line.strip()}
    except Exception as e:
        logger.error(f"加载历史文件失败: {e}")
        return set()

def save_processed_urls(urls_set):
    """保存更新后的URL历史记录"""
    try:
        with open(HISTORY_URL_FILE, 'w', encoding='utf-8') as f:
            for url in sorted(list(urls_set)):
                f.write(url + '\n')
    except Exception as e:
        logger.error(f"保存历史文件失败: {e}")


def get_all_download_tasks(url_list):
    """
    (步骤1) 顺序遍历所有详情页，收集所有需要下载的文件信息（URL和目标路径）。
    返回一个任务列表，每个任务是 (下载链接, 保存路径, 文件名) 的元组。
    """
    tasks = []
    if not url_list:
        return tasks
        
    if not os.path.exists(DOWNLOAD_DIR_TEMP):
        os.makedirs(DOWNLOAD_DIR_TEMP)
    
    for url in tqdm(url_list, desc="步骤 1/3 - 正在解析详情页面"):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                download_elements = soup.select('a.news_detail_download_item_link')
                for element in download_elements:
                    link = element.get('href')
                    name_tag = element.select_one('span.news_detail_download_item_text')
                    if not link or not name_tag:
                        continue
                    
                    # 清理文件名中的非法字符
                    file_name = name_tag.text.strip().replace('\r', '').replace('\n', '')
                    safe_file_name = re.sub(r'[\\/*?:"<>|]', "", file_name)
                    file_path = os.path.join(DOWNLOAD_DIR_TEMP, safe_file_name)
                    
                    # 检查文件是否已存在于临时目录或最终目录（防止重复下载）
                    if os.path.exists(file_path) or check_if_file_exists_in_final_dir(safe_file_name):
                        continue
                    
                    if not link.startswith('http'):
                        link = 'http:' + link
                    tasks.append((link, file_path, safe_file_name))
            time.sleep(random.uniform(0.5, 1.5)) # 解析页面间也需要延时
        except requests.RequestException as e:
            logger.error(f"访问详情页失败 {url}: {e}")
    return tasks

def check_if_file_exists_in_final_dir(filename):
    """检查文件是否已存在于最终分类目录的任何子文件夹中"""
    if not os.path.exists(DOWNLOAD_DIR_CLASSIFIED):
        return False
    for root, _, files in os.walk(DOWNLOAD_DIR_CLASSIFIED):
        if filename in files:
            return True
    return False

def download_single_file(url, path, filename):
    """下载单个文件的函数，供线程池调用"""
    try:
        response = requests.get(url, timeout=120, stream=True)
        response.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return f"成功: {filename}"
    except Exception as e:
        return f"失败: {filename} - {str(e)}"

def concurrent_downloader(tasks):
    """(步骤2) 使用线程池并发下载所有文件"""
    if not tasks:
        logger.info("没有新的文件需要下载。")
        return
        
    logger.info(f"发现 {len(tasks)} 个新文件，开始 {MAX_THREADS} 线程并发下载...")
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = [executor.submit(download_single_file, url, path, filename) for url, path, filename in tasks]
        # 使用tqdm创建进度条
        pbar = tqdm(as_completed(futures), total=len(tasks), desc="步骤 2/3 - 正在下载文件")
        for future in pbar:
            result = future.result()
            if "失败" in result:
                # 记录失败日志，但不显示在进度条上
                logger.warning(result)

def classify_downloaded_files():
    """(步骤3) 遍历临时目录，将文件按 学科/等级 分类到最终目录"""
    logger.info("\n========== 步骤 3/3 - 开始执行文件分类任务 ==========")
    if not os.path.exists(DOWNLOAD_DIR_TEMP):
        logger.info("临时下载目录不存在，无需分类。")
        return

    files_to_classify = [f for f in os.listdir(DOWNLOAD_DIR_TEMP) if os.path.isfile(os.path.join(DOWNLOAD_DIR_TEMP, f))]
    if not files_to_classify:
        logger.info("临时文件夹为空，无需分类。")
        try: os.rmdir(DOWNLOAD_DIR_TEMP)
        except OSError: pass # 如果目录非空（比如有隐藏文件），则忽略
        return
        
    for filename in tqdm(files_to_classify, desc="步骤 3/3 - 正在分类文件"):
        source_path = os.path.join(DOWNLOAD_DIR_TEMP, filename)
        subject, level = "未分类", "未分类"

        for keyword, sub_name in SUBJECT_KEYWORDS.items():
            if keyword in filename.lower():
                subject = sub_name
                break
        
        level_match = re.search(r'([一二三四五六七八九\d]+)级', filename)
        if level_match:
            level_str = level_match.group(1)
            level = f"{CHINESE_NUMERALS.get(level_str, level_str)}级"
        
        target_dir = os.path.join(DOWNLOAD_DIR_CLASSIFIED, subject, level)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        
        target_path = os.path.join(target_dir, filename)
        shutil.move(source_path, target_path)
    
    logger.info("分类完成！")
    try:
        if not os.listdir(DOWNLOAD_DIR_TEMP):
            os.rmdir(DOWNLOAD_DIR_TEMP)
            logger.info(f"已清理空的临时目录: {DOWNLOAD_DIR_TEMP}")
    except Exception as e:
        logger.error(f"清理临时目录失败: {e}")

def main_job():
    """主任务流程，协调所有操作"""
    logger.info("========== 脚本开始执行 ==========")
    processed_urls = load_processed_urls()
    logger.info(f"已加载 {len(processed_urls)} 条历史URL记录。")

    all_found_urls = get_urls_from_sources()
    new_urls_to_process = list(set(all_found_urls) - processed_urls)
    logger.info(f"本次运行发现 {len(new_urls_to_process)} 个新的详情页需要处理。")

    download_tasks = get_all_download_tasks(new_urls_to_process)
    concurrent_downloader(download_tasks)
    classify_downloaded_files()
    
    updated_processed_urls = processed_urls.union(new_urls_to_process)
    if len(updated_processed_urls) > len(processed_urls):
        save_processed_urls(updated_processed_urls)
        logger.info(f"URL历史记录已更新，总计 {len(updated_processed_urls)} 条。")

    logger.info("========== 所有任务执行完毕 ==========")

if __name__ == "__main__":
    try:
        main_job()
    except KeyboardInterrupt:
        logger.info("脚本被用户手动中断。")
    except Exception as e:
        logger.error(f"脚本顶层运行时捕获到意外错误: {e}", exc_info=True)
