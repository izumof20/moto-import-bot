import os
import pandas as pd
import streamlit as st
import io
import json
import time
import ssl
import requests
import threading
import shutil
import difflib
from openai import OpenAI
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# Налаштування SSL
ssl._create_default_https_context = ssl._create_unverified_context
load_dotenv()

# --- СТРУКТУРА СТОВПЧИКІВ ДЛЯ PHP ІМПОРТУ (СУВОРИЙ ПОРЯДОК 0-63) ---
PHP_PRODUCT_COLUMNS = [
    'Артикул', 'Родительский артикул', 'Артикул модели', 'Название модификации (UA)', 'Название модификации (RU)',
    'Название (UA)', 'Название (RU)', 'Бренд', 'Раздел', 'Цена', 'Опт', 'Старая цена', 'Валюта', 'Отображать',
    'Наличие', 'Дополнительные разделы', 'Фото', 'Галерея', 'Обзор 360', 'Алиас', 'Ссылка', 'Дата добавления',
    'Единицы измерения', 'HTML title (UA)', 'HTML title (RU)', 'META keywords (UA)', 'META keywords (RU)',
    'META description (UA)', 'META description (RU)', 'h1 заголовок (UA)', 'h1 заголовок (RU)', 'Поставщик',
    'Иконки', 'Популярность', 'Описание товара (UA)', 'Описание товара (RU)', 'Скидка %', 'Количество',
    'Короткое описание (UA)', 'Короткое описание (RU)', 'Тип гарантии', 'Гарантийный срок, мес.', 'Цвет',
    'Дата и время окончания акции', 'Текст акции (UA)', 'Текст акции (RU)', 'Описание для маркетплейсов (UA)',
    'Описание для маркетплейсов (RU)', 'Выгружать на маркетплейсы', 'Штрихкод', 'Состояние товара',
    'Код производителя товара (MPN)', 'Только для взрослых', 'На складе для Prom', '«Покупка частями» от monobank',
    '«Оплата частями» ПриватБанка', 'Уникальный код налога', 'Размер', 'Размер джинс', 'Размер мотобот',
    'Размер куртки', 'Размер штанов', 'Аксессуары(товары)', 'Аксессуары(разделы)'
]

STATUS_FILE = "process_status.json"
AI_CACHE_FILE = "ai_cache.json"
CAT_CACHE_FILE = "cat_cache.json"
UPLOADS_DIR = "uploads" 

if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)

def save_status(is_running=False, current=0, total=0, done=False, error=None, start_time=None, stats=None):
    data = {'is_running': is_running, 'current_idx': current, 'total': total, 'done': done, 'error': error, 'start_time': start_time, 'stats': stats}
    with open(STATUS_FILE, "w") as f: json.dump(data, f)

def load_status():
    if not os.path.exists(STATUS_FILE): return {'is_running': False, 'current_idx': 0, 'total': 0, 'done': False, 'error': None, 'start_time': None, 'stats': None}
    try:
        with open(STATUS_FILE, "r") as f: status = json.load(f)
        if status.get('start_time') and (time.time() - status['start_time'] > 86400):
            new_status = {'is_running': False, 'current_idx': 0, 'total': 0, 'done': False, 'error': None, 'start_time': None, 'stats': None}
            save_status(**new_status); return new_status
        return status
    except: return {'is_running': False, 'current_idx': 0, 'total': 0, 'done': False, 'error': None, 'start_time': None, 'stats': None}

def send_telegram_results(text, files_paths=None):
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        if files_paths:
            for p in files_paths:
                if os.path.exists(p):
                    with open(p, 'rb') as f: requests.post(f"https://api.telegram.org/bot{token}/sendDocument", data={"chat_id": chat_id}, files={"document": f})
    except: pass

def fetch_page_html(url):
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=15)
        if resp.status_code == 200: return resp.text
    except: pass
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200: return resp.text
    except: pass
    return ""

def background_worker(df_main, df_spec, use_ai, api_key, output_dir, stats_info):
    try:
        client = OpenAI(api_key=api_key)
        start_t = time.time()
        
        seo_cols = ['Описание товара (UA)', 'Описание товара (RU)', 'HTML title (UA)', 'HTML title (RU)', 'META description (UA)', 'META description (RU)', 'META keywords (UA)', 'META keywords (RU)']
        for col in seo_cols:
            if col in df_main.columns: df_main[col] = df_main[col].astype(object)

        df_spec['Артикул'] = df_spec['Артикул'].astype(str).str.strip()
        char_cols = [c for c in df_spec.columns if c not in ['Артикул', 'Название(UA)', 'Название(RU)']]
        df_spec['specs_summary'] = df_spec.apply(lambda r: "; ".join([f"{c}: {r[c]}" for c in char_cols if pd.notna(r[c])]), axis=1)
        df_work = df_main.merge(df_spec[['Артикул', 'specs_summary']], on='Артикул', how='left')
        
        parents = df_work[df_work['Артикул'].astype(str).str.strip() == df_work['Родительский артикул'].astype(str).str.strip()]
        if parents.empty: parents = df_work.drop_duplicates(subset=['Родительский артикул'])
        
        unique_cats = df_main['Раздел'].dropna().unique()
        total_steps = len(parents) + len(unique_cats)
        curr_step = 0
        
        ai_cache, cat_cache = {}, {}
        if os.path.exists(AI_CACHE_FILE):
            try:
                with open(AI_CACHE_FILE, "r") as f: ai_cache = json.load(f)
            except: pass
        if os.path.exists(CAT_CACHE_FILE):
            try:
                with open(CAT_CACHE_FILE, "r") as f: cat_cache = json.load(f)
            except: pass

        parsed_cat_images = {}
        for lang_prefix in ["", "ru/", "ua/", "uk/"]:
            html_text = fetch_page_html(f"https://moto-motion.com.ua/{lang_prefix}")
            if html_text:
                soup = BeautifulSoup(html_text, 'html.parser')
                for img in soup.find_all('img'):
                    alt = img.get('alt', '').strip().lower()
                    if not alt: continue
                    src = img.get('data-src') or img.get('src', '')
                    srcset = img.get('data-srcset') or img.get('srcset', '')
                    img_url = ""
                    if srcset: img_url = srcset.split(',')[0].split(' ')[0].strip()
                    elif src: img_url = src.strip()
                    if img_url and not img_url.startswith('data:image'):
                        if img_url.startswith('/'): img_url = "https://moto-motion.com.ua" + img_url
                        parsed_cat_images[alt] = img_url
            time.sleep(0.5)

        if use_ai:
            for idx, row in parents.iterrows():
                curr_step += 1
                save_status(is_running=True, current=curr_step, total=total_steps, start_time=start_t, stats=stats_info)
                p_art = str(row['Родительский артикул']).strip()
                
                if p_art in ai_cache: continue

                specs = df_work[df_work['Артикул'] == row['Артикул']]['specs_summary'].values[0] if row['Артикул'] in df_work['Артикул'].values else ""
                orig_desc = str(row.get('Описание товара (UA)', '')).strip()
                if orig_desc == 'nan': orig_desc = ""
                
                system_prompt = "Ти топовий маркетолог та копірайтер у сфері мотоекіпірування. Твоя мета - створювати об'ємні, експертні та продаючі описи товарів."
                prompt = f"""
                Товар: {row.get('Название (UA)', 'Товар')}
                Технічні характеристики: {specs}
                Базовий опис (факти від постачальника): {orig_desc[:1500]}
                
                НАПИШИ SEO JSON: {{"desc_ua": "...", "desc_meta_ua": "...", "key_ua": "...", "desc_ru": "...", "desc_meta_ru": "...", "key_ru": "..."}}
                
                ВИМОГИ ДО ОПИСІВ (desc_ua та desc_ru):
                1. Зроби текст РОЗГОРНУТИМ, цікавим та експертним (мінімум 1000-1500 символів).
                2. Обов'язково використовуй марковані списки (HTML теги <ul><li>) для ключових особливостей.
                3. Збережи всі важливі факти з базового опису (якщо вони є), але перепиши їх унікально для SEO.
                4. Поясни користь товару (комфорт, безпека, для яких умов підходить).
                5. Текст має бути красиво структурований абзацами (теги <p>).
                """
                try:
                    resp = client.chat.completions.create(model="gpt-4o-mini", response_format={"type": "json_object"}, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}])
                    ai_cache[p_art] = json.loads(resp.choices[0].message.content)
                    with open(AI_CACHE_FILE, "w") as f: json.dump(ai_cache, f, ensure_ascii=False)
                except Exception as e:
                    print(f"[УВАГА] Помилка на товарі {p_art}: {e}. Пропускаємо!")
                    # ЗБЕРІГАЄМО ПУСТИШКУ, ЩОБ РОЗІРВАТИ ЦИКЛ ПОМИЛОК!
                    ai_cache[p_art] = {"desc_ua": orig_desc, "desc_meta_ua": "", "key_ua": "", "desc_ru": "", "desc_meta_ru": "", "key_ru": ""}
                    with open(AI_CACHE_FILE, "w") as f: json.dump(ai_cache, f, ensure_ascii=False)
                    continue # Йдемо далі, конвеєр не зупиняється

            for idx, row in df_main.iterrows():
                p_art = str(row['Родительский артикул']).strip()
                if p_art in ai_cache:
                    r = ai_cache[p_art]
                    if r.get('desc_ua'): df_main.at[idx, 'Описание товара (UA)'] = str(r.get('desc_ua', ''))
                    df_main.at[idx, 'META description (UA)'] = str(r.get('desc_meta_ua', ''))
                    df_main.at[idx, 'META keywords (UA)'] = str(r.get('key_ua', ''))
                    if r.get('desc_ru'): df_main.at[idx, 'Описание товара (RU)'] = str(r.get('desc_ru', ''))
                    df_main.at[idx, 'META description (RU)'] = str(r.get('desc_meta_ru', ''))
                    df_main.at[idx, 'META keywords (RU)'] = str(r.get('key_ru', ''))
        else:
            curr_step += len(parents)
            save_status(is_running=True, current=curr_step, total=total_steps, start_time=start_t, stats=stats_info)

        cat_seo_list = []
        for cat in unique_cats:
            curr_step += 1
            save_status(is_running=True, current=curr_step, total=total_steps, start_time=start_t, stats=stats_info)
            cat_clean_name = str(cat).split('/')[-1].strip()
            cat_clean_lower = cat_clean_name.lower()
            
            img_link = parsed_cat_images.get(cat_clean_lower, "")
            if not img_link:
                best_match, highest_ratio = "", 0.0
                for alt_text, link in parsed_cat_images.items():
                    if not link: continue
                    if len(cat_clean_lower) > 3 and (cat_clean_lower in alt_text or alt_text in cat_clean_lower):
                        img_link = link; break
                    ratio = difflib.SequenceMatcher(None, cat_clean_lower, alt_text).ratio()
                    if ratio > highest_ratio: highest_ratio, best_match = ratio, link
                if not img_link and highest_ratio > 0.55: img_link = best_match
            
            if use_ai:
                if cat in cat_cache:
                    c = cat_cache[cat]
                else:
                    cat_prompt = f"SEO для категорії: '{cat}'. ПОВЕРНИ JSON: {{'title_ua': 'Title', 'desc_ua': 'Desc', 'key_ua': 'key', 'text_ua': 'TEXT', 'title_ru': 'Title', 'desc_ru': 'Desc', 'key_ru': 'key', 'text_ru': 'TEXT', 'h1_ru': 'Name'}}"
                    try:
                        resp = client.chat.completions.create(model="gpt-4o-mini", response_format={"type": "json_object"}, messages=[{"role": "user", "content": cat_prompt}])
                        c = json.loads(resp.choices[0].message.content)
                        cat_cache[cat] = c
                        with open(CAT_CACHE_FILE, "w") as f: json.dump(cat_cache, f, ensure_ascii=False)
                    except Exception as e: 
                        print(f"[УВАГА] Помилка на категорії {cat}: {e}. Пропускаємо!")
                        c = {"h1_ru": cat_clean_name, "text_ua": "", "title_ua": "", "desc_ua": "", "key_ua": "", "text_ru": "", "title_ru": "", "desc_ru": "", "key_ru": ""}
                        cat_cache[cat] = c
                        with open(CAT_CACHE_FILE, "w") as f: json.dump(cat_cache, f, ensure_ascii=False)
                        # continue не треба, бо це останній блок у циклі
                
                ru_name = c.get("h1_ru", cat_clean_name).strip()
                ru_name_lower = ru_name.lower()
                if not img_link and ru_name_lower in parsed_cat_images: img_link = parsed_cat_images[ru_name_lower]
                if not img_link:
                    ru_best, ru_max = "", 0.0
                    for alt_text, link in parsed_cat_images.items():
                        if not link: continue
                        if len(ru_name_lower) > 3 and (ru_name_lower in alt_text or alt_text in ru_name_lower):
                            img_link = link; break
                        ratio = difflib.SequenceMatcher(None, ru_name_lower, alt_text).ratio()
                        if ratio > ru_max: ru_max, ru_best = ratio, link
                    if not img_link and ru_max > 0.55: img_link = ru_best

                cat_seo_list.append({"Раздел": cat, "H1 (UA)": cat_clean_name, "Описание (UA)": c.get("text_ua"), "Meta Title (UA)": c.get("title_ua"), "Meta Description (UA)": c.get("desc_ua"), "Meta Keywords (UA)": c.get("key_ua"), "H1 (RU)": ru_name, "Описание (RU)": c.get("text_ru"), "Meta Title (RU)": c.get("title_ru"), "Meta Description (RU)": c.get("desc_ru"), "Meta Keywords (RU)": c.get("key_ru"), "Изображение": img_link})
            else:
                cat_seo_list.append({"Раздел": cat, "H1 (UA)": cat_clean_name, "Описание (UA)":"", "Meta Title (UA)":"", "Meta Description (UA)":"", "Meta Keywords (UA)":"", "H1 (RU)": cat_clean_name, "Описание (RU)":"", "Meta Title (RU)":"", "Meta Description (RU)":"", "Meta Keywords (RU)":"", "Изображение": img_link})
        
        for col in PHP_PRODUCT_COLUMNS:
            if col not in df_main.columns: df_main[col] = ""
        df_main = df_main[PHP_PRODUCT_COLUMNS]

        if not os.path.exists(output_dir): os.makedirs(output_dir)
        p31, psp, pct = f"{output_dir}/31.xlsx", f"{output_dir}/hid_specifications.xlsx", f"{output_dir}/categories_seo.xlsx"
        
        df_main.to_excel(p31, index=False)
        df_spec.drop(columns=['specs_summary'], errors='ignore').to_excel(psp, index=False)
        pd.DataFrame(cat_seo_list).to_excel(pct, index=False)

        duration = round((time.time() - start_t) / 60, 1)
        ai_status_text = "УВІМКНЕНО" if use_ai else "ВИМКНЕНО"

        full_msg = (
            f"<b>✅ MotoSvit готово!</b>\n\n"
            f"⏱ <b>Час:</b> {duration} хв.\n"
            f"🤖 <b>ChatGPT:</b> {ai_status_text}\n"
            f"📸 <b>Знайдено фото:</b> {len(parsed_cat_images)} шт.\n"
            f"📦 <b>Всього у постачальника:</b> {stats_info['supplier_total']}\n"
            f"✅ <b>В наявності (вибірка):</b> {stats_info['processed_in_stock']}\n"
            f"👨‍👦 <b>Батьківських моделей:</b> {stats_info['processed_parents']}\n"
            f"📂 <b>Унікальних категорій:</b> {stats_info['processed_cats']}\n\n"
            f"<i>Файли прикріплені нижче.</i>"
        )
        send_telegram_results(full_msg, [p31, psp, pct])
        save_status(done=True, is_running=False, start_time=start_t, stats=stats_info)
        
    except Exception as e:
        # СЮДИ БОТ ПОТРАПИТЬ ТІЛЬКИ ЯКЩО СТАНЕТЬСЯ КРИТИЧНА СИСТЕМНА ПОМИЛКА, А НЕ ШІ
        save_status(error=str(e), is_running=False, current=curr_step, total=total_steps)
        send_telegram_results(f"❌ Роботу зупинено: {e}")

# --- CSS ТА ОФОРМЛЕННЯ ---
st.set_page_config(page_title="MotoImport AI", layout="wide")
st.markdown("""
<style>
    .stApp { background-color: #F8FAFC; }
    h1 { color: #0F172A; text-align: center; font-weight: 800; }
    .btn-resume > button { background: linear-gradient(90deg, #10B981 0%, #059669 100%); color: white; border-radius: 8px; font-weight: 700; width: 100%; height: 60px; font-size: 18px; }
    .btn-start > button { background: linear-gradient(90deg, #FF416C 0%, #FF4B2B 100%); color: white; border-radius: 8px; font-weight: 700; width: 100%; height: 50px; }
    @keyframes izum-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(1.1); } }
    .izum-footer-wrapper { display: flex; justify-content: center; margin-top: 50px; padding: 20px; border-top: 1px solid rgba(226, 232, 240, 0.4); }
    .izum-badge { display: flex; align-items: center; gap: 8px; background-color: rgba(241, 245, 249, 0.2); padding: 6px 12px; border-radius: 9999px; border: 1px solid rgba(226, 232, 240, 0.4); backdrop-filter: blur(4px); transition: all 0.3s ease; font-family: system-ui, -apple-system, sans-serif; font-size: 14px; color: #0f172a; }
    .izum-badge:hover { background-color: rgba(241, 245, 249, 0.4); border-color: rgba(226, 232, 240, 0.6); }
    .izum-badge span.crafted-text { opacity: 0.8; }
    .izum-link { display: flex; align-items: center; gap: 4px; font-weight: 600; color: inherit; text-decoration: none; transition: color 0.3s ease; }
    .izum-link:hover { color: #FF416C; }
    .izum-icon { width: 14px; height: 14px; color: #FF416C; transition: transform 0.3s ease; }
    .izum-link:hover .izum-icon { animation: izum-pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
</style>
""", unsafe_allow_html=True)

def start_pipeline(main_path, spec_path, limit, in_stock_only, ai_on):
    if os.path.exists("output"): shutil.rmtree("output")
    os.makedirs("output")
    df_supplier, df_spec = pd.read_excel(main_path), pd.read_excel(spec_path)
    df_supplier.columns = df_supplier.columns.astype(str).str.strip()
    df_main = df_supplier.copy()
    if in_stock_only: df_main = df_main[~df_main['Наличие'].astype(str).str.lower().str.contains('немає|нет', na=False)].copy()
    if limit != "Всі": df_main = df_main.head(int(limit)).copy()
    p_check = df_main[df_main['Артикул'].astype(str).str.strip() == df_main['Родительский артикул'].astype(str).str.strip()]
    if p_check.empty and not df_main.empty: p_check = df_main.drop_duplicates(subset=['Родительский артикул'])
    stats = {'supplier_total': len(df_supplier), 'processed_in_stock': len(df_main), 'processed_parents': len(p_check), 'processed_cats': df_main['Раздел'].nunique()}
    save_status(is_running=True, current=0, total=100, start_time=time.time(), stats=stats)
    threading.Thread(target=background_worker, args=(df_main, df_spec, ai_on, os.getenv("OPENAI_API_KEY"), "output", stats)).start(); st.rerun()

def main():
    st.markdown("<h1>🏍️ MotoImport AI: Автономний Конвеєр</h1>", unsafe_allow_html=True)
    status = load_status()
    
    main_saved_path = os.path.join(UPLOADS_DIR, "31.xlsx")
    spec_saved_path = os.path.join(UPLOADS_DIR, "specifications.xlsx")
    has_saved_files = os.path.exists(main_saved_path) and os.path.exists(spec_saved_path)
    is_paused = status.get('error') is not None or (status.get('current_idx', 0) > 0 and not status['is_running'] and not status['done'])

    if not status['is_running'] and not status['done']:
        if is_paused and has_saved_files:
            st.warning(f"⚠️ Виявлено перервану сесію! Робота зупинилася на кроці {status.get('current_idx', 0)} з {status.get('total', 0)}.")
            st.info("Вам не потрібно завантажувати файли наново. Вони надійно збережені в пам'яті.")
        else:
            with st.container(border=True):
                c1, c2 = st.columns(2)
                with c1: main_file = st.file_uploader("Завантаж 31.xlsx (Товари)", type=["xlsx"])
                with c2: spec_file = st.file_uploader("Specifications", type=["xlsx"])

        with st.expander("⚙️ Налаштування обробки", expanded=True):
            cs1, cs2 = st.columns(2)
            with cs1:
                in_stock_only = st.checkbox("✅ Тільки в наявності", value=True)
                ai_on = st.checkbox("🤖 Активувати ChatGPT", value=True)
            with cs2:
                limit = st.selectbox("Ліміт рядків для обробки:", [10, 100, 500, 1000, 5000, "Всі"], index=5)

        c_start, c_clear = st.columns([3, 1])
        with c_start:
            if is_paused and has_saved_files:
                st.markdown('<div class="btn-resume">', unsafe_allow_html=True)
                if st.button("▶️ ПРОДОВЖИТИ З ПЕРЕРВАНОГО МІСЦЯ"):
                    start_pipeline(main_saved_path, spec_saved_path, limit, in_stock_only, ai_on)
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="btn-start">', unsafe_allow_html=True)
                if st.button("🚀 ЗАПУСТИТИ КОНВЕЄР"):
                    if main_file and spec_file:
                        with open(main_saved_path, "wb") as f: f.write(main_file.getvalue())
                        with open(spec_saved_path, "wb") as f: f.write(spec_file.getvalue())
                        start_pipeline(main_saved_path, spec_saved_path, limit, in_stock_only, ai_on)
                    else:
                        st.error("Будь ласка, завантажте обидва файли!")
                st.markdown('</div>', unsafe_allow_html=True)

        with c_clear:
            if st.button("🗑️ Почати з нуля (Очистити все)"):
                if os.path.exists(AI_CACHE_FILE): os.remove(AI_CACHE_FILE)
                if os.path.exists(CAT_CACHE_FILE): os.remove(CAT_CACHE_FILE)
                if os.path.exists(UPLOADS_DIR): shutil.rmtree(UPLOADS_DIR)
                save_status(is_running=False, done=False, current=0)
                st.success("Все очищено! Бот почне генерацію з чистого аркуша.")
                time.sleep(1)
                st.rerun()

    elif status['is_running']:
        with st.container(border=True):
            st.markdown("### ⚡️ Обробка товарів та категорій...")
            curr, total = status.get('current_idx', 0), status.get('total', 1)
            st.progress(curr / total if total > 0 else 0)
            st.metric("Крок", f"{curr} з {total}")
            st.info("💡 Кеш зберігається автоматично. Навіть якщо вкладка закриється, бот продовжить свою роботу.")
            time.sleep(3); st.rerun()

    elif status['done']:
        st.success("✅ ОБРОБКУ УСПІШНО ЗАВЕРШЕНО!")
        with st.container(border=True):
            cl1, cl2, cl3 = st.columns(3)
            with cl1:
                if os.path.exists("output/31.xlsx"): st.download_button("📂 Товари (31.xlsx)", open("output/31.xlsx", "rb"), "31.xlsx")
            with cl2:
                if os.path.exists("output/hid_specifications.xlsx"): st.download_button("📋 Характеристики", open("output/hid_specifications.xlsx", "rb"), "hid_specifications.xlsx")
            with cl3:
                if os.path.exists("output/categories_seo.xlsx"): st.download_button("🏷️ SEO Категорії", open("output/categories_seo.xlsx", "rb"), "categories_seo.xlsx")
            if st.button("🔄 Новий запит"): 
                if os.path.exists(UPLOADS_DIR): shutil.rmtree(UPLOADS_DIR)
                save_status(is_running=False, done=False)
                st.rerun()

    if status['error']:
        st.error(f"❌ Роботу зупинено через помилку: {status['error']}")
        st.warning("💡 Тексти, згенеровані до помилки, ЗБЕРЕЖЕНО в кеш! Просто натисніть «Продовжити з перерваного місця» — бот миттєво пройде готові товари і продовжить роботу.")

    st.markdown("""<div class="izum-footer-wrapper"><div class="izum-badge"><span class="crafted-text">Crafted with</span><a href="http://izumof.in.ua" target="_blank" rel="noopener noreferrer" class="izum-link"><svg class="izum-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>iZum</span></a></div></div>""", unsafe_allow_html=True)

if __name__ == "__main__": main()
