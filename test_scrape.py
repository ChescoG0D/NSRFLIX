import asyncio
from scrapers.pelisplus import PelisPlusScraper
from bs4 import BeautifulSoup
import aiohttp
import json

async def test_scrape():
    scraper = PelisPlusScraper()
    
    async with aiohttp.ClientSession() as session:
        # Test 1: Marriagetoxin (Anime)
        url_anime = "https://www.pelisplushd.la/anime/25393-marriagetoxin-online-espanol"
        print(f"Fetching {url_anime}...")
        async with session.get(url_anime, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
            html = await resp.text()
            soup = BeautifulSoup(html, 'html.parser')
            details = scraper.parse_details(soup, "25393-marriagetoxin-online-espanol.html", "anime")
            print("MARRIAGETOXIN DETAILS:")
            print(json.dumps(details, indent=2))

        # Test 2: El Halcon (Movie classified as series)
        url_movie = "https://www.pelisplushd.la/pelicula/25362-el-halcon-online-espanol"
        print(f"\nFetching {url_movie}...")
        async with session.get(url_movie, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
            html = await resp.text()
            soup = BeautifulSoup(html, 'html.parser')
            details = scraper.parse_details(soup, "25362-el-halcon-online-espanol.html", "series")
            print("EL HALCON DETAILS:")
            print(json.dumps(details, indent=2))

if __name__ == "__main__":
    asyncio.run(test_scrape())
