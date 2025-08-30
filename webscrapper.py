import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, urljoin
import io
import time
from duckduckgo_search import DDGS
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
import pyperclip

# ---------------------- Category Keywords ----------------------
CATEGORY_KEYWORDS = {
    "Skincare": ["skincare", "skin care", "face cream", "moisturizer", "serum", "lotion", "cleanser"],
    "Hair Care": ["shampoo", "conditioner", "hair oil", "haircare", "hair mask"],
    "Footwear": ["shoes", "sneakers", "sandals", "boots", "footwear"],
    "Fragnances": [
        "perfume", "fragrance", "cologne", "deodorant", "scent",
        "aroma", "incense", "essential oil", "diffuser", "attar", "room spray", "aromatherapy"
    ],
    "Personal Care": ["personal care", "toothpaste", "soap", "body wash", "hygiene"],
    "Clothing": ["clothing", "apparel", "t-shirt", "jeans", "dress", "fashion", "wear"],
    "Electronics": ["laptop", "mobile", "electronics", "gadgets", "headphones", "smartphone"],
    "Jewelry": ["jewelry", "ring", "necklace", "bracelet", "gold", "silver"],
    "Furniture": ["furniture", "sofa", "table", "chair", "interior"],
    "Food & Beverages": ["food", "beverage", "snacks", "drink", "restaurant", "cafe"],
    "Other": []
}

# ---------------------- Scraper Logic ----------------------
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def fetch_html(url, timeout=15):
    resp = requests.get(
        url,
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
        timeout=timeout,
        allow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text

def clean_emails(emails):
    valid_emails = []
    for email in emails:
        email = email.lower()
        if any(email.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg"]):
            continue
        if "@" not in email:
            continue
        if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$", email):
            valid_emails.append(email)
    return list(set(valid_emails))

def clean_phones(phones, max_count=3):
    valid_phones = []
    for p in phones:
        digits = re.sub(r"\D", "", p)
        if len(digits) == 10 and digits[0] in "6789":
            valid_phones.append(digits)
    return list(dict.fromkeys(valid_phones))[:max_count]

def extract_contact_info(html, max_phones=3):
    clean_html = re.sub(r'<(script|style).*?>.*?</\1>', '', html, flags=re.DOTALL|re.IGNORECASE)
    raw_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", clean_html)
    emails = clean_emails(raw_emails)
    raw_phones = re.findall(r"(?:\+91[\s-]*)?[6-9](?:[\s-]*\d){9}", clean_html)
    phones = clean_phones(raw_phones, max_phones)
    return emails, phones

def get_soup(html):
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")

def extract_category(soup):
    text = ""
    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta_desc and meta_desc.get("content"):
        text = meta_desc["content"].lower()
    elif soup.title and soup.title.string:
        text = soup.title.string.lower()

    # Match with keyword dictionary
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category

    return "Other"

def scrape_contact_page(base_url, max_phones=3):
    try:
        contact_url = urljoin(base_url, "/contact")
        html = fetch_html(contact_url, timeout=8)
        emails, phones = extract_contact_info(html, max_phones)
        return emails, phones
    except:
        return [], []

def scrape_website(url, max_phones=3):
    try:
        html = fetch_html(url)
        soup = get_soup(html)
        emails, phones = extract_contact_info(html, max_phones)

        # Try contact page for better accuracy
        if not emails:
            c_emails, c_phones = scrape_contact_page(url, max_phones)
            emails = emails or c_emails
            phones = phones or c_phones

        category = extract_category(soup)
        return {
            "Website": url,
            "Emails": ", ".join(emails),
            "Phone Numbers": ", ".join(phones),
            "Category": category
        }
    except Exception as e:
        return {
            "Website": url,
            "Emails": "",
            "Phone Numbers": "",
            "Category": f"Failed to fetch: {e}"
        }

def get_website_from_search(company_name):
    try:
        q = f"{company_name} official website"
        with DDGS() as ddgs:
            for r in ddgs.text(q, max_results=1, region="in-en", safesearch="moderate"):
                href = r.get("href") or r.get("link")
                if href:
                    return href
    except Exception:
        pass
    return None

# ---------------------- Streamlit UI ----------------------
def main():
    st.set_page_config(page_title="Company Info Scraper", layout="wide")
    st.title("🏢 Company Info & Contact Scraper")

    # Sidebar settings
    st.sidebar.header("⚙️ Settings")
    max_phones = st.sidebar.slider("Max phones per company", 1, 5, 3)
    timeout = st.sidebar.slider("Request timeout (seconds)", 5, 20, 12)

    if "results_df" not in st.session_state:
        st.session_state["results_df"] = pd.DataFrame()

    input_method = st.radio("Choose input method:", ["Upload Excel File", "Enter Website URLs Directly"], horizontal=True)

    # --- Excel Input ---
    if input_method == "Upload Excel File":
        uploaded_file = st.file_uploader("Upload Excel with a column 'Company'", type=["xlsx"])
        if uploaded_file:
            df = pd.read_excel(uploaded_file)
            col_map = {c.lower().strip(): c for c in df.columns}
            company_col = col_map.get("company")
            if not company_col:
                st.error("Excel must contain a column named 'Company'.")
                return
            companies = pd.Series(df[company_col]).dropna().astype(str).str.strip().tolist()

            if st.button("🚀 Start Scraping from Excel"):
                result_data = []
                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, company in enumerate(companies, start=1):
                    status_text.info(f"🔍 Searching website for: {company}")
                    website = get_website_from_search(company)
                    if not website:
                        result_data.append({"Company": company, "Website": "Not Found", "Emails": "", "Phone Numbers": "", "Category": "Website not found"})
                        continue
                    status_text.info(f"🌐 Scraping {website}")
                    data = scrape_website(website, max_phones)
                    data["Company"] = company
                    result_data.append(data)
                    progress_bar.progress(i / len(companies))
                    time.sleep(0.3)

                # Append to existing results instead of overwriting
                if st.session_state["results_df"].empty:
                    st.session_state["results_df"] = pd.DataFrame(result_data)
                else:
                    st.session_state["results_df"] = pd.concat([st.session_state["results_df"], pd.DataFrame(result_data)], ignore_index=True)

                st.success("✅ Scraping Complete")

    # --- Manual URL Input ---
    else:
        urls_text = st.text_area("Enter website URLs (one per line):", height=150, placeholder="https://example.com\nhttps://another.com")
        single_url = st.text_input("Or enter a single URL:", placeholder="https://example.com")

        if st.button("🚀 Start Scraping URLs"):
            urls_to_scrape = []
            if urls_text.strip():
                urls_to_scrape.extend([u.strip() for u in urls_text.splitlines() if u.strip()])
            if single_url.strip():
                urls_to_scrape.append(single_url.strip())

            if not urls_to_scrape:
                st.warning("Please provide at least one URL.")
                return

            def normalize(u):
                return u if u.startswith(("http://", "https://")) else "https://" + u
            urls_to_scrape = [normalize(u) for u in urls_to_scrape]

            result_data = []
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, url in enumerate(urls_to_scrape, start=1):
                parsed_url = urlparse(url)
                company_name = parsed_url.netloc.replace("www.", "").split(".")[0]

                status_text.info(f"🌐 Scraping {i}/{len(urls_to_scrape)}: {url}")
                data = scrape_website(url, max_phones)
                data["Company"] = company_name
                result_data.append(data)

                progress_bar.progress(i / len(urls_to_scrape))
                time.sleep(0.3)

            if st.session_state["results_df"].empty:
                st.session_state["results_df"] = pd.DataFrame(result_data)
            else:
                st.session_state["results_df"] = pd.concat([st.session_state["results_df"], pd.DataFrame(result_data)], ignore_index=True)

            st.success("✅ Scraping Complete")

    # ---------------- Always Display Results ----------------
    st.markdown("---")
    st.subheader("📊 Scraped Results")

    if not st.session_state["results_df"].empty:
        df = st.session_state["results_df"]

        # AgGrid interactive table
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_pagination(enabled=True)
        gb.configure_side_bar()
        gb.configure_selection("multiple", use_checkbox=True)
        gb.configure_default_column(editable=True, wrapText=True, autoHeight=True)
        grid_options = gb.build()

        grid_response = AgGrid(
            df,
            gridOptions=grid_options,
            update_mode=GridUpdateMode.MODEL_CHANGED,
            allow_unsafe_jscode=True,
            theme="dark",
            fit_columns_on_grid_load=True,
        )

        updated_df = grid_response["data"]
        selected = grid_response["selected_rows"]

        st.session_state["results_df"] = pd.DataFrame(updated_df)

        # Bulk actions
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("❌ Delete Selected"):
                if selected:
                    ids = [s["_selectedRowNodeInfo"]["nodeRowIndex"] for s in selected]
                    st.session_state["results_df"].drop(ids, inplace=True)
                    st.session_state["results_df"].reset_index(drop=True, inplace=True)
                    st.rerun()
        with col2:
            if st.button("🗑️ Clear All"):
                st.session_state["results_df"] = pd.DataFrame()
                st.rerun()
        with col3:
            if st.button("📋 Copy Emails to Clipboard"):
                all_emails = ", ".join(st.session_state["results_df"]["Emails"].tolist())
                pyperclip.copy(all_emails)
                st.success("📋 Emails copied to clipboard!")

        # Download options
        st.subheader("⬇️ Download Results")
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        excel_buffer.seek(0)

        st.download_button("📥 Download Excel", data=excel_buffer, file_name="scraped_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("📥 Download CSV", data=df.to_csv(index=False), file_name="scraped_results.csv", mime="text/csv")
        st.download_button("📥 Download JSON", data=df.to_json(orient="records", indent=2), file_name="scraped_results.json", mime="application/json")

    else:
        st.info("ℹ️ No results yet. Scrape some websites to see data here.")

if __name__ == "__main__":
    main()
