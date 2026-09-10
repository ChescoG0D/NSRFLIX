import aiohttp
import asyncio
import sys

async def fetch_and_save():
    url = "https://repelishd.fit/ver-pelicula/25393-marriagetoxin-online-espanol.html"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                html = await resp.text()
                with open('marriagetoxin.html', 'w', encoding='utf-8') as f:
                    f.write(html)
                print("HTML saved successfully!")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(fetch_and_save())
