// 할 일 관리 앱 JavaScript

// DOM 요소
const todoInput = document.getElementById('todoInput');
const categorySelect = document.getElementById('categorySelect');
const addButton = document.getElementById('addButton');
const todoList = document.getElementById('todoList');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const filterButtons = document.querySelectorAll('.filter-tabs__button');

// 상태
let todos = [];
let currentFilter = '전체';
let editingId = null;

// 카테고리 CSS 클래스 매핑
const categoryClass = {
    '업무': 'category--work',
    '개인': 'category--personal',
    '공부': 'category--study'
};

// localStorage 사용 가능 여부 확인
function isLocalStorageAvailable() {
    try {
        const test = '__storage_test__';
        localStorage.setItem(test, test);
        localStorage.removeItem(test);
        return true;
    } catch (e) {
        return false;
    }
}

// localStorage 저장
function saveTodos() {
    if (!isLocalStorageAvailable()) {
        console.warn('localStorage를 사용할 수 없습니다. 데이터가 저장되지 않습니다.');
        return;
    }
    try {
        localStorage.setItem('todos', JSON.stringify(todos));
    } catch (e) {
        console.error('데이터 저장 실패:', e);
    }
}

// localStorage 불러오기
function loadTodos() {
    if (!isLocalStorageAvailable()) {
        console.warn('localStorage를 사용할 수 없습니다.');
        todos = [];
        return;
    }
    try {
        const saved = localStorage.getItem('todos');
        if (saved) {
            todos = JSON.parse(saved);
        }
    } catch (e) {
        console.error('데이터 불러오기 실패:', e);
        todos = [];
    }
}

// 할 일 추가
function addTodo() {
    const title = todoInput.value.trim();
    if (!title) {
        todoInput.focus();
        return;
    }

    const todo = {
        id: Date.now(),
        title: title,
        category: categorySelect.value,
        completed: false,
        createdAt: new Date().toISOString()
    };

    todos.push(todo);
    todoInput.value = '';
    todoInput.focus();

    saveTodos();
    renderTodos();
    updateProgress();
}

// 할 일 삭제
function deleteTodo(id) {
    if (!confirm('정말 삭제하시겠습니까?')) {
        return;
    }
    todos = todos.filter(todo => todo.id !== id);
    saveTodos();
    renderTodos();
    updateProgress();
}

// 완료 상태 토글
function toggleTodo(id) {
    const todo = todos.find(todo => todo.id === id);
    if (todo) {
        todo.completed = !todo.completed;
        saveTodos();
        renderTodos();
        updateProgress();
    }
}

// 수정 모드 시작
function editTodo(id) {
    editingId = id;
    renderTodos();
}

// 수정 저장
function saveEdit(id) {
    const todo = todos.find(todo => todo.id === id);
    if (!todo) return;

    const editInput = document.getElementById(`edit-input-${id}`);
    const editCategory = document.getElementById(`edit-category-${id}`);

    const newTitle = editInput.value.trim();
    if (!newTitle) {
        editInput.focus();
        return;
    }

    todo.title = newTitle;
    todo.category = editCategory.value;
    editingId = null;
    saveTodos();
    renderTodos();
    updateProgress();
}

// 수정 취소
function cancelEdit() {
    editingId = null;
    renderTodos();
}

// 할 일 목록 렌더링
function renderTodos() {
    // 필터링
    let filteredTodos = currentFilter === '전체'
        ? [...todos]
        : todos.filter(todo => todo.category === currentFilter);

    // 미완료 항목 먼저 표시
    filteredTodos.sort((a, b) => a.completed - b.completed);

    todoList.innerHTML = filteredTodos.map(todo => {
        const cssClass = categoryClass[todo.category];
        const isEditing = editingId === todo.id;

        if (isEditing) {
            // 수정 모드
            return `
                <li class="todo-item editing" data-category="${todo.category}">
                    <input
                        type="text"
                        class="todo-item__edit-input"
                        id="edit-input-${todo.id}"
                        value="${escapeHtml(todo.title)}"
                        onkeypress="if(event.key === 'Enter') saveEdit(${todo.id})"
                    >
                    <select class="todo-item__edit-category" id="edit-category-${todo.id}">
                        <option value="업무" ${todo.category === '업무' ? 'selected' : ''}>업무</option>
                        <option value="개인" ${todo.category === '개인' ? 'selected' : ''}>개인</option>
                        <option value="공부" ${todo.category === '공부' ? 'selected' : ''}>공부</option>
                    </select>
                    <button class="todo-item__save" onclick="saveEdit(${todo.id})">저장</button>
                    <button class="todo-item__cancel" onclick="cancelEdit()">취소</button>
                </li>
            `;
        } else {
            // 일반 모드
            return `
                <li class="todo-item ${todo.completed ? 'completed' : ''}" data-category="${todo.category}">
                    <input
                        type="checkbox"
                        class="todo-item__checkbox"
                        ${todo.completed ? 'checked' : ''}
                        onchange="toggleTodo(${todo.id})"
                    >
                    <span class="todo-item__text">${escapeHtml(todo.title)}</span>
                    <span class="todo-item__category ${cssClass}">${todo.category}</span>
                    <button class="todo-item__edit" onclick="editTodo(${todo.id})">수정</button>
                    <button class="todo-item__delete" onclick="deleteTodo(${todo.id})">삭제</button>
                </li>
            `;
        }
    }).join('');

    // 수정 모드일 때 입력 필드에 포커스
    if (editingId) {
        const editInput = document.getElementById(`edit-input-${editingId}`);
        if (editInput) {
            editInput.focus();
            editInput.select();
        }
    }
}

// 진행률 업데이트 (현재 필터 기준)
function updateProgress() {
    const filteredTodos = currentFilter === '전체'
        ? todos
        : todos.filter(todo => todo.category === currentFilter);

    const total = filteredTodos.length;
    const completed = filteredTodos.filter(todo => todo.completed).length;
    const percentage = total === 0 ? 0 : (completed / total) * 100;

    progressFill.style.width = `${percentage}%`;
    progressText.textContent = `${completed}/${total} 완료`;
}

// 필터 변경
function setFilter(filter) {
    currentFilter = filter;

    filterButtons.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });

    renderTodos();
    updateProgress();
}

// HTML 이스케이프 (XSS 방지)
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 이벤트 리스너
addButton.addEventListener('click', addTodo);

todoInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        addTodo();
    }
});

filterButtons.forEach(btn => {
    btn.addEventListener('click', () => {
        setFilter(btn.dataset.filter);
    });
});

// 초기화
function init() {
    loadTodos();
    renderTodos();
    updateProgress();
}

init();
