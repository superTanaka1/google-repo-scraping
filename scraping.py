
import argparse
import re
import sqlite3
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://github.com/orgs/google/repositories"
DB_FILE = Path("db/google_repositories_final.sqlite")

HEADERS = {"User-Agent": "Mozilla/5.0"}

WAIT_SECONDS = 30
PAGE_SIZE = 30
MAX_PAGES = 150


# HTMLからリポジトリ情報を取得する
def parse_repositories(html):
    soup = BeautifulSoup(html, "html.parser")
    repositories = []

    repo_list = soup.select('li[class*="listItem"]')

    for repo in repo_list:

        name_tag = repo.select_one(
            'h3 a[href^="/google/"]'
        )

        if name_tag is None:
            continue

        href = name_tag.get("href", "")

        if not re.fullmatch(r"/google/[^/]+", href):
            continue

        name = href.split("/")[-1]

        language_tag = repo.select_one(
            'span[class*="PrimaryLanguageName"]'
        )

        if language_tag:
            language = language_tag.get_text(strip=True)
        else:
            language = "不明"

        star_tag = repo.select_one(
            'a[href$="/stargazers"]'
        )

        if star_tag is None:
            raise ValueError(
                f"{name}のスター数が見つかりません"
            )

        star_text = star_tag.get_text(
            " ", strip=True
        )

        if not star_text:
            raise ValueError(
                f"{name}のスター数が空です"
            )

        stars = star_text.split()[0]

        repositories.append(
            (name, language, stars)
        )

    names = [row[0] for row in repositories]

    if len(names) != len(set(names)):
        raise ValueError(
            "同じページに重複したリポジトリがあります"
        )

    return repositories


# データベースを準備する
def prepare_database(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS repositories (
            name TEXT PRIMARY KEY,
            language TEXT,
            stars TEXT
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS fetched_pages (
            page INTEGER PRIMARY KEY,
            count INTEGER NOT NULL
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    con.commit()


# SELECT文でデータを表示する
def show_data(con):
    print("\n----- 保存したデータ -----")

    cur = con.execute("""
        SELECT name, language, stars
        FROM repositories
        ORDER BY name;
    """)

    for row in cur:
        print(row)

    count = con.execute("""
        SELECT COUNT(*) FROM repositories;
    """).fetchone()[0]

    print("\n保存件数:", count)


# GitHubからスクレイピングする
def scrape(con, test_mode=False):

    finished = con.execute("""
        SELECT value FROM settings
        WHERE key = 'finished';
    """).fetchone()

    if finished and finished[0] == "yes":
        print("最終ページ候補まで取得済みです")
        return

    # 保存済みのページを調べる
    page_number = 1

    while con.execute(
        "SELECT 1 FROM fetched_pages WHERE page = ?;",
        (page_number,)
    ).fetchone():
        page_number += 1

    print("開始ページ:", page_number)

    previous_names = None
    pages_this_run = 0

    with requests.Session() as session:

        while page_number <= MAX_PAGES:

            # 授業指定のsleep
            time.sleep(1)

            # さらに待機してアクセス間隔を空ける
            time.sleep(WAIT_SECONDS - 1)

            print(f"\n{page_number}ページ目を取得中...")

            try:
                response = session.get(
                    URL,
                    params={"page": page_number},
                    headers=HEADERS,
                    timeout=30
                )

                # アクセス制限があれば再試行しない
                if response.status_code in (429, 403):
                    print("アクセス制限を検出しました")
                    print("ステータス:", response.status_code)
                    print(
                        "Retry-After:",
                        response.headers.get("Retry-After")
                    )
                    print("アクセスを停止します")
                    break

                response.raise_for_status()

            except requests.exceptions.RequestException as e:
                print("通信エラー:", e)
                break

            try:
                page_data = parse_repositories(
                    response.content
                )

            except ValueError as e:
                print("HTML解析エラー:", e)
                break

            if not page_data:
                print("リポジトリが見つかりません")
                print("取得完了とは判断せず停止します")
                break

            names = tuple(row[0] for row in page_data)

            if names == previous_names:
                print("前のページと同じ内容です")
                print("安全のため停止します")
                break

            # SQLiteへ1ページ分を保存
            with con:
                con.executemany("""
                    INSERT OR REPLACE INTO repositories
                    (name, language, stars)
                    VALUES (?, ?, ?);
                """, page_data)

                con.execute("""
                    INSERT INTO fetched_pages
                    (page, count)
                    VALUES (?, ?);
                """, (page_number, len(page_data)))

                if len(page_data) < PAGE_SIZE:
                    con.execute("""
                        INSERT OR REPLACE INTO settings
                        (key, value)
                        VALUES ('finished', 'yes');
                    """)

            count = con.execute("""
                SELECT COUNT(*) FROM repositories;
            """).fetchone()[0]

            print("このページの件数:", len(page_data))
            print("DB保存済み件数:", count)

            pages_this_run += 1

            # 最終ページの候補
            if len(page_data) < PAGE_SIZE:
                print("最終ページ候補に到達しました")
                print("総件数との照合が必要です")
                break

            # テスト時は1ページのみ
            if test_mode and pages_this_run >= 1:
                print("1ページのテストを終了しました")
                break

            page_number += 1

        else:
            print("ページ数の上限に到達しました")


# 通信しないテスト
def offline_check(con):

    sample = """
    <li class="listItem__sample">
        <h3><a href="/google/example">example</a></h3>
        <span class="PrimaryLanguageName__sample">
            Python
        </span>
        <a href="/google/example/stargazers">1.2k</a>
    </li>
    """

    result = parse_repositories(sample)

    assert result == [
        ("example", "Python", "1.2k")
    ]

    print("HTML解析テスト: 成功")
    print("テストデータ:", result)

    count = con.execute("""
        SELECT COUNT(*) FROM repositories;
    """).fetchone()[0]

    print("現在のDB保存件数:", count)
    print("GitHubへのアクセスは行っていません")


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["check", "test", "all", "show"]
    )

    args = parser.parse_args()

    DB_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    con = sqlite3.connect(DB_FILE)

    try:
        prepare_database(con)

        if args.mode == "check":
            offline_check(con)

        elif args.mode == "show":
            show_data(con)

        elif args.mode == "test":
            scrape(con, test_mode=True)
            show_data(con)

        elif args.mode == "all":
            scrape(con)
            show_data(con)

    finally:
        con.close()


if __name__ == "__main__":
    main()
