import sqlite3
import os

DATABASE = 'recipe.db'


def get_db():
    """데이터베이스 연결 반환"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """데이터베이스 초기화"""
    conn = get_db()
    cursor = conn.cursor()

    # 사용자 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            dietary_restrictions TEXT DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 저장된 레시피 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS saved_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recipe_name TEXT NOT NULL,
            recipe_data TEXT NOT NULL,
            rating INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # 레시피 히스토리 테이블
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recipe_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ingredients TEXT NOT NULL,
            recipes_generated TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()


def get_user_by_email(email):
    """이메일로 사용자 조회"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    """ID로 사용자 조회"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, email, dietary_restrictions, created_at FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def create_user(email, password_hash):
    """새 사용자 생성"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO users (email, password_hash) VALUES (?, ?)',
            (email, password_hash)
        )
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def update_dietary_restrictions(user_id, restrictions):
    """식이 제한 업데이트"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE users SET dietary_restrictions = ? WHERE id = ?',
        (restrictions, user_id)
    )
    conn.commit()
    conn.close()


def save_recipe(user_id, recipe_name, recipe_data):
    """레시피 저장"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO saved_recipes (user_id, recipe_name, recipe_data) VALUES (?, ?, ?)',
        (user_id, recipe_name, recipe_data)
    )
    conn.commit()
    recipe_id = cursor.lastrowid
    conn.close()
    return recipe_id


def get_saved_recipes(user_id):
    """저장된 레시피 목록 조회"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM saved_recipes WHERE user_id = ? ORDER BY created_at DESC',
        (user_id,)
    )
    recipes = cursor.fetchall()
    conn.close()
    return recipes


def get_saved_recipe_by_id(recipe_id, user_id):
    """특정 저장된 레시피 조회"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM saved_recipes WHERE id = ? AND user_id = ?',
        (recipe_id, user_id)
    )
    recipe = cursor.fetchone()
    conn.close()
    return recipe


def delete_saved_recipe(recipe_id, user_id):
    """저장된 레시피 삭제"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM saved_recipes WHERE id = ? AND user_id = ?',
        (recipe_id, user_id)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def update_recipe_rating_notes(recipe_id, user_id, rating=None, notes=None):
    """레시피 평점/메모 업데이트"""
    conn = get_db()
    cursor = conn.cursor()

    if rating is not None and notes is not None:
        cursor.execute(
            'UPDATE saved_recipes SET rating = ?, notes = ? WHERE id = ? AND user_id = ?',
            (rating, notes, recipe_id, user_id)
        )
    elif rating is not None:
        cursor.execute(
            'UPDATE saved_recipes SET rating = ? WHERE id = ? AND user_id = ?',
            (rating, recipe_id, user_id)
        )
    elif notes is not None:
        cursor.execute(
            'UPDATE saved_recipes SET notes = ? WHERE id = ? AND user_id = ?',
            (notes, recipe_id, user_id)
        )

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# 앱 시작 시 DB 초기화
if __name__ == '__main__':
    init_db()
    print('Database initialized successfully.')
