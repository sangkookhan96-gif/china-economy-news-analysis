import os
import json
import re
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from openrouter_client import analyze_image, chat
from database import (
    init_db, get_user_by_email, get_user_by_id, create_user,
    update_dietary_restrictions, save_recipe, get_saved_recipes,
    delete_saved_recipe, update_recipe_rating_notes
)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# uploads 폴더 생성 및 DB 초기화
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
init_db()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_ingredients(response_text):
    """AI 응답에서 재료 목록을 추출"""
    # JSON 형식 추출 시도
    json_match = re.search(r'\{[^{}]*"ingredients"[^{}]*\}', response_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if 'ingredients' in data:
                return data['ingredients']
        except json.JSONDecodeError:
            pass

    # JSON 배열만 있는 경우
    array_match = re.search(r'\[([^\[\]]+)\]', response_text)
    if array_match:
        try:
            items = json.loads(array_match.group())
            if isinstance(items, list):
                return items
        except json.JSONDecodeError:
            pass

    # 줄바꿈으로 구분된 목록 파싱
    lines = response_text.strip().split('\n')
    ingredients = []
    for line in lines:
        line = line.strip()
        # 번호나 불릿 제거
        line = re.sub(r'^[\d\.\-\*\•]+\s*', '', line)
        if line and len(line) < 50:  # 너무 긴 줄은 제외
            ingredients.append(line)

    return ingredients if ingredients else ["재료를 인식하지 못했습니다"]


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/analyze-image', methods=['POST'])
def api_analyze_image():
    if 'image' not in request.files:
        return jsonify({'error': '이미지 파일이 없습니다'}), 400

    file = request.files['image']

    if file.filename == '':
        return jsonify({'error': '선택된 파일이 없습니다'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '지원하지 않는 파일 형식입니다'}), 400

    try:
        # 파일 저장
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # 이미지 분석
        prompt = """이 냉장고/식품 이미지에서 보이는 모든 식재료를 인식해주세요.
다음 JSON 형식으로만 응답해주세요:
{"ingredients": ["재료1", "재료2", "재료3"]}

JSON 외에 다른 텍스트는 포함하지 마세요."""

        response = analyze_image(filepath, prompt, model="google/gemma-3-27b-it:free")

        # 파일 삭제 (임시 파일)
        os.remove(filepath)

        # 재료 목록 파싱
        ingredients = parse_ingredients(response)

        return jsonify({'ingredients': ingredients})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def parse_recipes(response_text):
    """AI 응답에서 레시피 목록을 추출"""
    # JSON 블록 추출 시도 (```json ... ``` 형식)
    json_block_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response_text)
    if json_block_match:
        try:
            data = json.loads(json_block_match.group(1))
            if 'recipes' in data:
                return data['recipes']
        except json.JSONDecodeError:
            pass

    # 전체 JSON 객체 추출 시도
    json_match = re.search(r'\{[\s\S]*"recipes"[\s\S]*\}', response_text)
    if json_match:
        try:
            # 중첩된 JSON 처리를 위해 가장 바깥 {} 찾기
            text = json_match.group()
            brace_count = 0
            start = -1
            end = -1
            for i, char in enumerate(text):
                if char == '{':
                    if start == -1:
                        start = i
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break
            if start != -1 and end != -1:
                data = json.loads(text[start:end])
                if 'recipes' in data:
                    return data['recipes']
        except json.JSONDecodeError:
            pass

    return None


@app.route('/api/generate-recipe', methods=['POST'])
def api_generate_recipe():
    data = request.get_json()

    if not data or 'ingredients' not in data:
        return jsonify({'error': '재료 목록이 없습니다'}), 400

    ingredients = data['ingredients']

    if not ingredients or len(ingredients) == 0:
        return jsonify({'error': '최소 하나 이상의 재료가 필요합니다'}), 400

    try:
        prompt = f"""다음 재료들로 만들 수 있는 요리 레시피 3개를 추천해주세요.

재료: {', '.join(ingredients)}

반드시 다음 JSON 형식으로만 응답해주세요. JSON 외에 다른 텍스트는 포함하지 마세요:
{{
  "recipes": [
    {{
      "name": "요리명",
      "difficulty": "쉬움",
      "time": "15분",
      "servings": "2인분",
      "ingredients": [
        {{"name": "재료명", "amount": "양"}}
      ],
      "steps": ["조리 단계 1", "조리 단계 2"],
      "missing_ingredients": ["부족한 재료"]
    }}
  ]
}}

difficulty는 "쉬움", "보통", "어려움" 중 하나입니다.
missing_ingredients는 주어진 재료에 없지만 요리에 필요한 기본 재료들입니다."""

        response = chat(prompt, model="deepseek/deepseek-r1-0528:free")

        recipes = parse_recipes(response)

        if recipes:
            return jsonify({'recipes': recipes})
        else:
            return jsonify({'error': '레시피 생성에 실패했습니다. 다시 시도해주세요.', 'raw': response}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============ 인증 API ============

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data = request.get_json()

    if not data or 'email' not in data or 'password' not in data:
        return jsonify({'error': '이메일과 비밀번호를 입력해주세요'}), 400

    email = data['email'].strip().lower()
    password = data['password']

    if not email or '@' not in email:
        return jsonify({'error': '유효한 이메일을 입력해주세요'}), 400

    if len(password) < 4:
        return jsonify({'error': '비밀번호는 4자 이상이어야 합니다'}), 400

    # 이미 존재하는 이메일 확인
    if get_user_by_email(email):
        return jsonify({'error': '이미 등록된 이메일입니다'}), 400

    # 사용자 생성
    password_hash = generate_password_hash(password)
    user_id = create_user(email, password_hash)

    if user_id:
        session['user_id'] = user_id
        return jsonify({
            'success': True,
            'user': {'id': user_id, 'email': email}
        })
    else:
        return jsonify({'error': '회원가입에 실패했습니다'}), 500


@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json()

    if not data or 'email' not in data or 'password' not in data:
        return jsonify({'error': '이메일과 비밀번호를 입력해주세요'}), 400

    email = data['email'].strip().lower()
    password = data['password']

    user = get_user_by_email(email)

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': '이메일 또는 비밀번호가 올바르지 않습니다'}), 401

    session['user_id'] = user['id']
    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'email': user['email'],
            'dietary_restrictions': json.loads(user['dietary_restrictions'] or '[]')
        }
    })


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.pop('user_id', None)
    return jsonify({'success': True})


@app.route('/api/auth/me', methods=['GET'])
def api_me():
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'user': None})

    user = get_user_by_id(user_id)

    if not user:
        session.pop('user_id', None)
        return jsonify({'user': None})

    return jsonify({
        'user': {
            'id': user['id'],
            'email': user['email'],
            'dietary_restrictions': json.loads(user['dietary_restrictions'] or '[]')
        }
    })


# ============ 프로필 API ============

@app.route('/api/profile', methods=['PATCH'])
def api_update_profile():
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'error': '로그인이 필요합니다'}), 401

    data = request.get_json()

    if 'dietary_restrictions' in data:
        restrictions = json.dumps(data['dietary_restrictions'])
        update_dietary_restrictions(user_id, restrictions)

    return jsonify({'success': True})


# ============ 레시피 저장 API ============

@app.route('/api/recipes', methods=['GET'])
def api_get_recipes():
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'error': '로그인이 필요합니다'}), 401

    recipes = get_saved_recipes(user_id)
    result = []

    for recipe in recipes:
        result.append({
            'id': recipe['id'],
            'recipe_name': recipe['recipe_name'],
            'recipe_data': json.loads(recipe['recipe_data']),
            'rating': recipe['rating'],
            'notes': recipe['notes'],
            'created_at': recipe['created_at']
        })

    return jsonify({'recipes': result})


@app.route('/api/recipes', methods=['POST'])
def api_save_recipe():
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'error': '로그인이 필요합니다'}), 401

    data = request.get_json()

    if not data or 'recipe_data' not in data:
        return jsonify({'error': '레시피 데이터가 없습니다'}), 400

    recipe_data = data['recipe_data']
    recipe_name = recipe_data.get('name', '이름 없는 레시피')

    recipe_id = save_recipe(user_id, recipe_name, json.dumps(recipe_data))

    return jsonify({'success': True, 'recipe_id': recipe_id})


@app.route('/api/recipes/<int:recipe_id>', methods=['DELETE'])
def api_delete_recipe(recipe_id):
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'error': '로그인이 필요합니다'}), 401

    deleted = delete_saved_recipe(recipe_id, user_id)

    if deleted:
        return jsonify({'success': True})
    else:
        return jsonify({'error': '레시피를 찾을 수 없습니다'}), 404


@app.route('/api/recipes/<int:recipe_id>', methods=['PATCH'])
def api_update_recipe(recipe_id):
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'error': '로그인이 필요합니다'}), 401

    data = request.get_json()
    rating = data.get('rating')
    notes = data.get('notes')

    updated = update_recipe_rating_notes(recipe_id, user_id, rating, notes)

    if updated:
        return jsonify({'success': True})
    else:
        return jsonify({'error': '레시피를 찾을 수 없습니다'}), 404


if __name__ == '__main__':
    app.run(debug=True, port=5000)
