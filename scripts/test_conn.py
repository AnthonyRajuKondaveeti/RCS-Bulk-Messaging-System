import psycopg2
import sys

def test():
    try:
        print("Testing connection to 127.0.0.1:5433...")
        conn = psycopg2.connect(
            host="127.0.0.1",
            port=5433,
            database="rcs_platform_dev",
            user="postgres",
            password="rcs_dev_pass"
        )
        print("Connection SUCCESSFUL!")
        cur = conn.cursor()
        cur.execute("SELECT version();")
        print("PostgreSQL version:", cur.fetchone())
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Connection FAILED: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test()
