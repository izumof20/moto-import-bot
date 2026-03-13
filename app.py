import os
import pandas as pd
import streamlit as st
import io
import json
from openai import OpenAI
from dotenv import load_dotenv

# Завантажуємо змінні середовища
load_dotenv()

# ==========================================
# CSS-СТИЛІЗАЦІЯ ІНТЕРФЕЙСУ
# ==========================================
custom_css = """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stApp { background-color: #F8FAFC; }
    h1 { color: #0F172A; font-weight: 800 !important; text-align: center; padding-bottom: 10px; }
    .stButton > button {
        background: linear-gradient(90deg, #FF416C 0%, #FF4B2B 100%);
        color: white !important; border: none; border-radius: 8px;
        font-weight: 700; font-size: 16px; padding: 12px 24px;
        box-shadow: 0 4px 15px rgba(255, 75, 43, 0.4);
        transition: all 0.3s ease; width: 100%;
    }
    .stDownloadButton > button {
        background: linear-gradient(90deg, #11998E 0%, #38EF7D 100%);
        color: white !important; border: none; border-radius: 8px;
        font-weight: 700; box-shadow: 0 4px 15px rgba(56, 239, 125, 0.4);
        transition: all 0.3s ease; width: 100%;
    }
</style>
"""

def load_data(file):
    if file.name.endswith('.csv'):
        try:
            df = pd.read_csv(file)
            if len(df.columns) < 2:
                file.seek(0)
                df = pd.read_csv(file, sep=';')
        except Exception:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    else:
        return pd.read_excel(file)

def generate_ai_seo(api_key, product_name, specs, exist_ua, exist_ru):
    try:
        client = OpenAI(api_key=api_key)
        prompt = f"""
        Ти — професійний SEO-фахівець. Назва: {product_name}. Характеристики: {specs}. 
        Напиши унікальний HTML опис, Title, Meta Description та Keywords (UA та RU). 
        Видали посилання. ПОВЕРНИ JSON: 
        {{"desc_ua": "...", "desc_ru": "...", "title_ua": "...", "title_ru": "...", "meta_desc_ua": "...", "meta_desc_ru": "...", "keywords_ua": "...", "keywords_ru": "..."}}
        """
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": "SEO Expert JSON Only"}, {"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}

def main():
    st.set_page_config(page_title="MotoImport AI", layout="wide", page_icon="🏍️")
    st.markdown(custom_css, unsafe_allow_html=True)
    st.markdown("<h1>🏍️ MotoImport: SEO-Конвеєр</h1>", unsafe_allow_html=True)

    if 'processing_done' not in st.session_state: st.session_state['processing_done'] = False
    if 'log_messages' not in st.session_state: st.session_state['log_messages'] = []

    with st.container(border=True):
        st.markdown("### 📁 1. Завантаження файлів")
        col1, col2 = st.columns(2)
        with col1:
            main_file = st.file_uploader("Головний файл (ТОВАРИ)", type=["xlsx", "xls", "csv"])
        with col2:
            spec_file = st.file_uploader("Файл специфікацій", type=["xlsx", "xls", "csv"])

    if main_file and spec_file:
        with st.container(border=True):
            st.markdown("### ⚙️ 2. Налаштування")
            col_opt1, col_opt2 = st.columns(2)
            with col_opt1:
                exclude_oos = st.checkbox("🚫 Вирізати 'Немає в наявності'", value=True)
                use_ai = st.checkbox("🤖 Активувати ChatGPT (SEO + Описи)", value=False)
            with col_opt2:
                limit_choice = st.selectbox("Ліміт обробки:", [10, 50, 100, 500, "Всі"], index=4)
        
        if st.button("🚀 ЗАПУСТИТИ ОБРОБКУ"):
            try:
                st.session_state['log_messages'] = ["🔄 Запуск процесу..."]
                df_main = load_data(main_file)
                df_spec = load_data(spec_file)

                # Фільтрація
                if exclude_oos and 'Наличие' in df_main.columns:
                    df_main = df_main[~df_main['Наличие'].astype(str).str.lower().str.contains('нет|немає', na=False)]
                
                if limit_choice != "Всі":
                    df_main = df_main.head(int(limit_choice)).copy()

                # ВИПРАВЛЕННЯ ПОМИЛКИ DTYPE: Явне створення текстових колонок
                seo_cols = [
                    'Описание товара (UA)', 'Описание товара (RU)',
                    'HTML title (UA)', 'HTML title (RU)',
                    'META description (UA)', 'META description (RU)',
                    'META keywords (UA)', 'META keywords (RU)'
                ]
                for c in seo_cols:
                    df_main[c] = "" # Спочатку заповнюємо пустим
                    df_main[c] = df_main[c].astype(str) # ПРИМУСОВО РОБИМО ТЕКСТОМ

                if use_ai:
                    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
                    if not api_key:
                        st.error("❌ API Key не знайдено в Secrets!")
                        return

                    st.session_state['log_messages'].append("⏳ ШІ працює...")
                    
                    # Логіка обробки ШІ
                    df_main['Артикул'] = df_main['Артикул'].astype(str).str.strip()
                    df_main['Родительский артикул'] = df_main['Родительский артикул'].astype(str).str.strip()
                    df_spec['Артикул'] = df_spec['Артикул'].astype(str).str.strip()
                    
                    # Збираємо специфікації для промпта
                    char_cols = [c for c in df_spec.columns if c not in ['Артикул', 'Название(UA)', 'Название(RU)']]
                    df_spec['specs_summary'] = df_spec.apply(lambda r: "; ".join([f"{c}: {r[c]}" for c in char_cols if pd.notna(r[c])]), axis=1)
                    
                    df_main = df_main.merge(df_spec[['Артикул', 'specs_summary']], on='Артикул', how='left')

                    parents = df_main[df_main['Артикуl'] == df_main['Родительский артикул']]
                    ai_cache = {}
                    
                    pbar = st.progress(0)
                    for i, (idx, row) in enumerate(parents.iterrows()):
                        res = generate_ai_seo(api_key, row.get('Название (UA)', 'Товар'), row.get('specs_summary', ''), row.get('Описание товара (UA)', ''), row.get('Описание товара (RU)', ''))
                        ai_cache[row['Артикул']] = res
                        pbar.progress((i+1)/len(parents))

                    # Роздаємо результати всім рядкам
                    for idx, row in df_main.iterrows():
                        parent = row['Родительский артикул']
                        if parent in ai_cache:
                            r = ai_cache[parent]
                            df_main.at[idx, 'Описание товара (UA)'] = r.get('desc_ua', '')
                            df_main.at[idx, 'Описание товара (RU)'] = r.get('desc_ru', '')
                            df_main.at[idx, 'HTML title (UA)'] = r.get('title_ua', '')
                            df_main.at[idx, 'HTML title (RU)'] = r.get('title_ru', '')
                            df_main.at[idx, 'META description (UA)'] = r.get('meta_desc_ua', '')
                            df_main.at[idx, 'META description (RU)'] = r.get('meta_desc_ru', '')
                            df_main.at[idx, 'META keywords (UA)'] = r.get('keywords_ua', '')
                            df_main.at[idx, 'META keywords (RU)'] = r.get('keywords_ru', '')

                # ФОРМУВАННЯ ФАЙЛУ (Згідно з твоїм скріншотом)
                strict_cols = ['Артикул', 'Родительский артикул', 'Артикул модели', 'Название модификации (UA)', 'Название модификации (RU)', 'Название (UA)', 'Название (RU)', 'Бренд', 'Раздел', 'Цена', 'Опт', 'Старая цена', 'Валюта', 'Отображать', 'Наличие', 'Дополнительные разделы', 'Фото', 'Галерея', 'Обзор 360', 'Алиас', 'Ссылка', 'Дата добавления', 'Единицы измерения', 'HTML title (UA)', 'HTML title (RU)', 'META keywords (UA)', 'META keywords (RU)', 'META description (UA)', 'META description (RU)', 'h1 заголовок (UA)', 'h1 заголовок (RU)', 'Поставщик', 'Иконки', 'Популярность', 'Описание товара (UA)', 'Описание товара (RU)', 'Скидка %', 'Количество', 'Короткое описание (UA)', 'Короткое описание (RU)', 'Тип гарантии', 'Гарантийный срок, мес.', 'Цвет', 'Дата и время окончания акции', 'Текст акции (UA)', 'Текст акции (RU)', 'Описание для маркетплейсов (UA)', 'Описание для маркетплейсов (RU)', 'Выгружать на маркетплейсы', 'Штрихкод', 'Состояние товара', 'Код производителя товара (MPN)', 'Только для взрослых', 'На складе для Prom', '«Покупка частями» от monobank', '«Оплата частями» ПриватБанка', 'Уникальный код налога', 'Размер', 'Размер джинс', 'Размер мотобот', 'Размер куртки', 'Размер штанов', 'Аксессуары(товары)', 'Аксессуары(разделы)']
                
                for c in strict_cols:
                    if c not in df_main.columns: df_main[c] = ""
                
                final_df = df_main[strict_cols]
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    final_df.to_excel(writer, index=False)
                st.session_state['excel_data_main'] = output.getvalue()
                st.session_state['processing_done'] = True
                st.session_state['log_messages'].append("✅ Готово!")

            except Exception as e:
                st.error(f"❌ Помилка: {e}")

        # Вивід результатів
        if st.session_state['log_messages']:
            for m in st.session_state['log_messages']: st.info(m)
        if st.session_state['processing_done']:
            st.download_button("⬇️ ЗАВАНТАЖИТИ 31.xlsx", st.session_state['excel_data_main'], "31.xlsx")

    st.markdown("<br><hr><div style='text-align: center; color: gray; font-size: 12px;'>Created by <b>iZum</b></div>", unsafe_allow_html=True)

if __name__ == "__main__": main()
