import psycopg2
import os

from dotenv import load_dotenv
load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

def run():
    print("Conectando a la DB para eliminar falsas series de cinehdplus...")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    cur.execute("DELETE FROM movies WHERE title ILIKE '%serie%' AND provider = 'cinehdplus';")
    deleted = cur.rowcount
    conn.commit()
    
    print(f"Borradas {deleted} películas falsas que en realidad eran series.")
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    run()
