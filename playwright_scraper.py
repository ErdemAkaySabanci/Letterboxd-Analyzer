import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import pandas as pd
import json
import os
import re
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

DOMAIN = "https://letterboxd.com"

STAR_MAP = {
    "½": 0.5,
    "★": 1.0, "★½": 1.5,
    "★★": 2.0, "★★½": 2.5,
    "★★★": 3.0, "★★★½": 3.5,
    "★★★★": 4.0, "★★★★½": 4.5,
    "★★★★★": 5.0,
}

def convert_star_rating(text: str):
    return STAR_MAP.get(text.strip())

async def scrape_all_pages(username: str):
    records = []
    
    async with async_playwright() as p:
        # Use a real browser user agent and avoid headless detection flags if possible
        browser = await p.chromium.launch(headless=False) 
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Scrape first page
        url = f"{DOMAIN}/{username}/films/"
        print(f"Navigating to {url}")
        await page.goto(url)
        await page.wait_for_selector('ul.grid', timeout=30000)
        
        # Check total pages
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        page_links = soup.find_all("li", class_="paginate-page")
        num_pages = int(page_links[-1].find("a").get_text().strip()) if page_links else 1
        print(f"Total pages found: {num_pages}")
        
        def parse_page(soup):
            page_records = []
            grid = soup.find("ul", class_="grid")
            if not grid:
                return page_records
                
            for li in grid.find_all("li"):
                div = li.find("div")
                if not div: continue
                img = li.find("img")
                title = img.get("alt", "Unknown") if img else "Unknown"
                
                link = div.get("data-target-link") or div.get("data-item-link", "")
                if not link: continue
                
                movie_id = (div.get("data-film-id") or div.get("data-postered-identifier") or div.get("data-item-slug", ""))
                
                rating_p = li.find("p", class_="poster-viewingdata")
                rating_text = rating_p.get_text().strip() if rating_p else ""
                rating = convert_star_rating(rating_text)
                
                if rating is not None:
                    page_records.append({
                        "movie_id": movie_id,
                        "title_of_movie": title,
                        "my_rating": rating,
                        "link_of_movie": link,
                    })
            return page_records
            
        records.extend(parse_page(soup))
        
        # Go through pages
        for p_num in range(2, num_pages + 1):
            print(f"Scraping page {p_num}/{num_pages}...")
            url = f"{DOMAIN}/{username}/films/page/{p_num}/"
            await page.goto(url)
            # Wait to ensure Cloudflare checks pass (if any)
            await page.wait_for_selector('ul.grid', timeout=60000)
            await page.wait_for_timeout(2000) # Give it 2s to breathe
            
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            records.extend(parse_page(soup))
            
        await browser.close()
        
    df = pd.DataFrame(records)
    print(f"Total rated movies scraped: {len(df)}")
    df.to_csv("my_movie_dataset_full_urls.csv", index=False, encoding="utf-8")
    return df

if __name__ == "__main__":
    asyncio.run(scrape_all_pages("Erdemstein"))
