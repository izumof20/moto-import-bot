import os
import pandas as pd
import streamlit as st
import io
import json
import time
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Стилізація інтерфейсу
custom_css = """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stApp { background-color: #F8FAFC; }
    h1 { color: #0F172A; font-weight: 800; text-align: center; }
    .stButton > button {
        background: linear-gradient(90deg, #FF416C 0%, #FF4B2B 100%);
        color: white !important; border: none; border-radius: 8px;
        font-weight: 700; width: 100%; height: 50px;
    }
    .stDownloadButton > button {
        background: linear-gradient(90deg, #11998E 0%, #38EF7D 100%);
        color: white !important; border-radius: 8px; width: 100%;
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
        except:
            file.seek(0)
            df = pd.read_csv(file, sep=';')
        return df
    return pd.read_excel(file)

def generate_ai_seo(api_key, product_name, specs):
    try:
        client = OpenAI(api_key=api_key)
        prompt = f"""
        Ти — SEO-копірайтер. Товар: {product_name}. ТТХ: {specs}. 
        Напиши унікальний HTML опис, Title, Meta Desc, Keywords для UA та RU версій. 
        Видали посилання. ПОВЕРНИ JSON: 
        {{"desc_ua": "...", "desc_ru": "...", "title_ua": "...", "title_ru": "...", "meta_desc_ua": "...", "meta_desc_ru": "...", "keywords_ua": "...", "keywords_ru": "..."}}
        """
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": "SEO JSON Expert"}, {"role": "user", "content": prompt}]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return None

def main():
    st.set_page_config(page_title="MotoImport AI", layout="wide", page_icon="🏍️")
    st.markdown(custom_css, unsafe_allow_html=True)
    st.markdown("<h1>🏍️ MotoImport: SEO-Конвеєр</h1>", unsafe_allow_html=True)

    if 'processing_done' not in st.session_state: st.session_state['processing_done'] = False

    with st.container(border=True):
        st.markdown("### 📁 1. Завантаження файлів")
        col1, col2 = st.columns(2)
        with col1: main_file = st.file_uploader("Файл ТОВАРІВ", type=["xlsx", "xls", "csv"])
        with col2: spec_file = st.file_uploader("Файл СПЕЦИФІКАЦІЙ", type=["xlsx", "xls", "csv"])

    if main_file and spec_file:
        with st.container(border=True):
            st.markdown("### ⚙️ 2. Налаштування")
            col_opt1, col_opt2 = st.columns(2)
            with col_opt1:
                exclude_oos = st.checkbox("🚫 Вирізати 'Немає в наявності'", value=True)
                use_ai = st.checkbox("🤖 Активувати ChatGPT (SEO + Описи)", value=True)
            with col_opt2:
                # ВСТАНОВЛЕНО "Всі" ЯК ЗА ЗАМОВЧУВАННЯМ (index=4)
                limit_choice = st.selectbox("Ліміт обробки (для тесту):", [10, 50, 100, 500, "Всі"], index=4)
        
        if st.button("🚀 ЗАПУСТИТИ ОБРОБКУ"):
            try:
                df_main = load_data(main_file)
                df_spec = load_data(spec_file)

                # Фільтрація залишків
                if exclude_oos and 'Наличие' in df_main.columns:
                    df_main = df_main[~df_main['Наличие'].astype(str).str.lower().str.contains('нет|немає', na=False)]
                
                if limit_choice != "Всі":
                    df_main = df_main.head(int(limit_choice)).copy()

                # Підготовка колонок
                seo_cols = ['Описание товара (UA)', 'Описание товара (RU)', 'HTML title (UA)', 'HTML title (RU)', 'META description (UA)', 'META description (RU)', 'META keywords (UA)', 'META keywords (RU)']
                for c in seo_cols:
                    df_main[c] = ""
                    df_main[c] = df_main[c].astype(str)

                if use_ai:
                    api_key = st.secrets.get("OPENAI_API_KEY")
                    if not api_key:
                        st.error("Ключ OpenAI не знайдено!")
                        return

                    # Логіка кешування для варіацій
                    df_main['Артикул'] = df_main['Артикул'].astype(str).str.strip()
                    df_main['Родительский артикул'] = df_main['Родительский артикул'].astype(str).str.strip()
                    
                    # Збираємо ТТХ
                    char_cols = [c for c in df_spec.columns if c not in ['Артикул', 'Название(UA)', 'Название(RU)']]
                    df_spec['specs_summary'] = df_spec.apply(lambda r: "; ".join([f"{c}: {r[c]}" for c in char_cols if pd.notna(r[c])]), axis=1)
                    df_main = df_main.merge(df_spec[['Артикул', 'specs_summary']], on='Артикул', how='left')

                    # Знаходимо унікальні моделі (батьків)
                    parents = df_main[df_main['Артикул'] == df_main['Родительский артикул']]
                    if parents.empty: parents = df_main.drop_duplicates(subset=['Родительский артикул'])

                    ai_cache = {}
                    st.info(f"⏳ Обробка {len(parents)} унікальних моделей через ШІ...")
                    pbar = st.progress(0)
                    
                    for i, (idx, row) in enumerate(parents.iterrows()):
                        p_art = row['Родительский артикул']
                        res = generate_ai_seo(api_key, row.get('Название (UA)', 'Товар'), row.get('specs_summary', ''))
                        if res: ai_cache[p_art] = res
                        pbar.progress((i + 1) / len(parents))

                    # Роздаємо результати всім варіаціям
                    for idx, row in df_main.iterrows():
                        p_art = row['Родительский артикул']
                        if p_art in ai_cache:
                            r = ai_cache[p_art]
                            df_main.at[idx, 'Описание товара (UA)'] = r.get('desc_ua', '')
                            df_main.at[idx, 'Описание товара (RU)'] = r.get('desc_ru', '')
                            df_main.at[idx, 'HTML title (UA)'] = r.get('title_ua', '')
                            df_main.at[idx, 'HTML title (RU)'] = r.get('title_ru', '')
                            df_main.at[idx, 'META description (UA)'] = r.get('meta_desc_ua', '')
                            df_main.at[idx, 'META description (RU)'] = r.get('meta_desc_ru', '')
                            df_main.at[idx, 'META keywords (UA)'] = r.get('keywords_ua', '')
                            df_main.at[idx, 'META keywords (RU)'] = r.get('keywords_ru', '')

                # Збереження 31.xlsx
                out_main = io.BytesIO()
                df_main.to_excel(out_main, index=False)
                st.session_state['file_main'] = out_main.getvalue()

                # Збереження специфікацій
                out_spec = io.BytesIO()
                df_spec.to_excel(out_spec, index=False)
                st.session_state['file_spec'] = out_spec.getvalue()

                st.session_state['processing_done'] = True
                st.success("✅ Обробка завершена успішно!")

            except Exception as e:
                st.error(f"Помилка: {e}")

        if st.session_state['processing_done']:
            st.markdown("### ⬇️ 3. Завантаження результатів")
            res_col1, res_col2 = st.columns(2)
            with res_col1:
                st.download_button("📂 Завантажити 31.xlsx", st.session_state['file_main'], "31.xlsx")
            with res_col2:
                st.download_button("📋 Завантажити hid_specifications.xlsx", st.session_state['file_spec'], "hid_specifications.xlsx")

    st.markdown("<br><hr><div style='text-align: center; color: gray; font-size: 12px;'>Created by <b>iZum</b></div>", unsafe_allow_html=True)

if __name__ == "__main__": main()
