document.addEventListener('DOMContentLoaded', function() {
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    const previewArea = document.getElementById('previewArea');
    const previewImage = document.getElementById('previewImage');
    const removeImageBtn = document.getElementById('removeImage');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const loading = document.getElementById('loading');
    const resultSection = document.getElementById('resultSection');
    const ingredientsList = document.getElementById('ingredientsList');
    const newIngredientInput = document.getElementById('newIngredient');
    const addIngredientBtn = document.getElementById('addIngredientBtn');
    const getRecipeBtn = document.getElementById('getRecipeBtn');

    let selectedFile = null;
    let ingredients = [];

    // 업로드 영역 클릭
    uploadArea.addEventListener('click', () => fileInput.click());

    // 파일 선택
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });

    // 드래그 앤 드롭
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    // 파일 처리
    function handleFile(file) {
        if (!file.type.startsWith('image/')) {
            alert('이미지 파일만 업로드 가능합니다.');
            return;
        }

        if (file.size > 16 * 1024 * 1024) {
            alert('파일 크기는 16MB 이하여야 합니다.');
            return;
        }

        selectedFile = file;

        // 미리보기 표시
        const reader = new FileReader();
        reader.onload = (e) => {
            previewImage.src = e.target.result;
            uploadArea.style.display = 'none';
            previewArea.style.display = 'block';
            analyzeBtn.disabled = false;
        };
        reader.readAsDataURL(file);
    }

    // 이미지 제거
    removeImageBtn.addEventListener('click', () => {
        selectedFile = null;
        fileInput.value = '';
        previewArea.style.display = 'none';
        uploadArea.style.display = 'block';
        analyzeBtn.disabled = true;
        resultSection.style.display = 'none';
    });

    // 재료 인식
    analyzeBtn.addEventListener('click', async () => {
        if (!selectedFile) return;

        // UI 상태 변경
        analyzeBtn.disabled = true;
        loading.style.display = 'block';
        resultSection.style.display = 'none';

        const formData = new FormData();
        formData.append('image', selectedFile);

        try {
            const response = await fetch('/api/analyze-image', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (response.ok) {
                ingredients = data.ingredients;
                renderIngredients();
                resultSection.style.display = 'block';
            } else {
                alert('오류: ' + (data.error || '재료 인식에 실패했습니다.'));
            }
        } catch (error) {
            alert('네트워크 오류가 발생했습니다.');
            console.error(error);
        } finally {
            loading.style.display = 'none';
            analyzeBtn.disabled = false;
        }
    });

    // 재료 목록 렌더링
    function renderIngredients() {
        ingredientsList.innerHTML = '';

        ingredients.forEach((ingredient, index) => {
            const item = document.createElement('div');
            item.className = 'ingredient-item';
            item.innerHTML = `
                <input type="checkbox" id="ing-${index}" checked>
                <label for="ing-${index}">${ingredient}</label>
                <button class="remove-btn" data-index="${index}">&times;</button>
            `;
            ingredientsList.appendChild(item);

            // 체크박스 이벤트
            const checkbox = item.querySelector('input[type="checkbox"]');
            checkbox.addEventListener('change', () => {
                item.classList.toggle('unchecked', !checkbox.checked);
            });

            // 삭제 버튼 이벤트
            const removeBtn = item.querySelector('.remove-btn');
            removeBtn.addEventListener('click', () => {
                ingredients.splice(index, 1);
                renderIngredients();
            });
        });
    }

    // 재료 추가
    function addIngredient() {
        const newIngredient = newIngredientInput.value.trim();
        if (newIngredient && !ingredients.includes(newIngredient)) {
            ingredients.push(newIngredient);
            renderIngredients();
            newIngredientInput.value = '';
        }
    }

    addIngredientBtn.addEventListener('click', addIngredient);
    newIngredientInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            addIngredient();
        }
    });

    // 레시피 관련 요소
    const recipeLoading = document.getElementById('recipeLoading');
    const recipeSection = document.getElementById('recipeSection');
    const recipeCards = document.getElementById('recipeCards');
    const regenerateBtn = document.getElementById('regenerateBtn');

    // 모달 관련 요소
    const recipeModal = document.getElementById('recipeModal');
    const modalClose = document.getElementById('modalClose');
    const modalRecipeName = document.getElementById('modalRecipeName');
    const modalDifficulty = document.getElementById('modalDifficulty');
    const modalTime = document.getElementById('modalTime');
    const modalServings = document.getElementById('modalServings');
    const modalIngredients = document.getElementById('modalIngredients');
    const modalMissing = document.getElementById('modalMissing');
    const modalMissingList = document.getElementById('modalMissingList');
    const modalSteps = document.getElementById('modalSteps');

    let recipes = [];

    // 레시피 추천 버튼
    getRecipeBtn.addEventListener('click', generateRecipes);
    regenerateBtn.addEventListener('click', generateRecipes);

    async function generateRecipes() {
        const selectedIngredients = getSelectedIngredients();
        if (selectedIngredients.length === 0) {
            alert('최소 하나 이상의 재료를 선택해주세요.');
            return;
        }

        // UI 상태 변경
        getRecipeBtn.disabled = true;
        regenerateBtn.disabled = true;
        recipeLoading.style.display = 'block';
        recipeSection.style.display = 'none';

        try {
            const response = await fetch('/api/generate-recipe', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ ingredients: selectedIngredients })
            });

            const data = await response.json();

            if (response.ok) {
                recipes = data.recipes;
                renderRecipeCards();
                recipeSection.style.display = 'block';
            } else {
                alert('오류: ' + (data.error || '레시피 생성에 실패했습니다.'));
                console.error('Raw response:', data.raw);
            }
        } catch (error) {
            alert('네트워크 오류가 발생했습니다.');
            console.error(error);
        } finally {
            recipeLoading.style.display = 'none';
            getRecipeBtn.disabled = false;
            regenerateBtn.disabled = false;
        }
    }

    // 레시피 카드 렌더링
    function renderRecipeCards() {
        recipeCards.innerHTML = '';

        recipes.forEach((recipe, index) => {
            const card = document.createElement('div');
            card.className = 'recipe-card';

            const difficultyClass = getDifficultyClass(recipe.difficulty);
            const previewSteps = recipe.steps ? recipe.steps.slice(0, 2).join(' ').substring(0, 60) + '...' : '';

            card.innerHTML = `
                <h3>${recipe.name}</h3>
                <div class="card-meta">
                    <span class="badge ${difficultyClass}">${recipe.difficulty || '보통'}</span>
                    <span class="meta-item">${recipe.time || '30분'}</span>
                    <span class="meta-item">${recipe.servings || '2인분'}</span>
                </div>
                <p class="card-preview">${previewSteps}</p>
            `;

            card.addEventListener('click', () => openRecipeModal(index));
            recipeCards.appendChild(card);
        });
    }

    // 난이도 클래스 반환
    function getDifficultyClass(difficulty) {
        switch (difficulty) {
            case '쉬움': return 'badge-easy';
            case '어려움': return 'badge-hard';
            default: return 'badge-medium';
        }
    }

    // 레시피 모달 열기
    function openRecipeModal(index) {
        const recipe = recipes[index];

        modalRecipeName.textContent = recipe.name;
        modalDifficulty.textContent = recipe.difficulty || '보통';
        modalDifficulty.className = 'badge ' + getDifficultyClass(recipe.difficulty);
        modalTime.textContent = recipe.time || '30분';
        modalServings.textContent = recipe.servings || '2인분';

        // 재료 목록
        modalIngredients.innerHTML = '';
        if (recipe.ingredients && Array.isArray(recipe.ingredients)) {
            recipe.ingredients.forEach(ing => {
                const li = document.createElement('li');
                if (typeof ing === 'object') {
                    li.textContent = `${ing.name} ${ing.amount || ''}`;
                } else {
                    li.textContent = ing;
                }
                modalIngredients.appendChild(li);
            });
        }

        // 부족한 재료
        if (recipe.missing_ingredients && recipe.missing_ingredients.length > 0) {
            modalMissing.style.display = 'block';
            modalMissingList.innerHTML = '';
            recipe.missing_ingredients.forEach(ing => {
                const li = document.createElement('li');
                li.textContent = ing;
                modalMissingList.appendChild(li);
            });
        } else {
            modalMissing.style.display = 'none';
        }

        // 조리 순서
        modalSteps.innerHTML = '';
        if (recipe.steps && Array.isArray(recipe.steps)) {
            recipe.steps.forEach(step => {
                const li = document.createElement('li');
                // 단계 번호 제거 (이미 ol 태그로 번호가 붙음)
                li.textContent = step.replace(/^\d+\.\s*/, '');
                modalSteps.appendChild(li);
            });
        }

        recipeModal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    // 모달 닫기
    function closeModal() {
        recipeModal.style.display = 'none';
        document.body.style.overflow = '';
    }

    modalClose.addEventListener('click', closeModal);
    recipeModal.addEventListener('click', (e) => {
        if (e.target === recipeModal) {
            closeModal();
        }
    });

    // ESC 키로 모달 닫기
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (recipeModal.style.display === 'flex') closeModal();
            if (authModal.style.display === 'flex') closeAuthModal();
            if (mypageModal.style.display === 'flex') closeMypageModal();
            if (savedRecipeModal.style.display === 'flex') closeSavedRecipeModal();
        }
    });

    // 선택된 재료 가져오기
    function getSelectedIngredients() {
        const selected = [];
        const items = ingredientsList.querySelectorAll('.ingredient-item');
        items.forEach((item, index) => {
            const checkbox = item.querySelector('input[type="checkbox"]');
            if (checkbox.checked) {
                selected.push(ingredients[index]);
            }
        });
        return selected;
    }

    // ============ 인증 관련 ============

    // 인증 관련 요소
    const loginBtn = document.getElementById('loginBtn');
    const registerBtn = document.getElementById('registerBtn');
    const logoutBtn = document.getElementById('logoutBtn');
    const mypageBtn = document.getElementById('mypageBtn');
    const loggedOutButtons = document.getElementById('loggedOutButtons');
    const loggedInButtons = document.getElementById('loggedInButtons');
    const userEmailSpan = document.getElementById('userEmail');

    // 인증 모달 요소
    const authModal = document.getElementById('authModal');
    const authModalClose = document.getElementById('authModalClose');
    const authModalTitle = document.getElementById('authModalTitle');
    const authForm = document.getElementById('authForm');
    const authEmail = document.getElementById('authEmail');
    const authPassword = document.getElementById('authPassword');
    const authError = document.getElementById('authError');
    const authSubmitBtn = document.getElementById('authSubmitBtn');
    const authSwitchText = document.getElementById('authSwitchText');
    const authSwitchBtn = document.getElementById('authSwitchBtn');

    // 마이페이지 모달 요소
    const mypageModal = document.getElementById('mypageModal');
    const mypageModalClose = document.getElementById('mypageModalClose');
    const profileEmail = document.getElementById('profileEmail');
    const dietaryOptions = document.getElementById('dietaryOptions');
    const saveDietaryBtn = document.getElementById('saveDietaryBtn');
    const savedRecipesList = document.getElementById('savedRecipesList');

    // 저장된 레시피 모달 요소
    const savedRecipeModal = document.getElementById('savedRecipeModal');
    const savedRecipeModalClose = document.getElementById('savedRecipeModalClose');
    const ratingInput = document.getElementById('ratingInput');
    const recipeNotes = document.getElementById('recipeNotes');
    const saveNotesBtn = document.getElementById('saveNotesBtn');
    const deleteRecipeBtn = document.getElementById('deleteRecipeBtn');

    // 레시피 저장 버튼
    const saveRecipeBtn = document.getElementById('saveRecipeBtn');

    let currentUser = null;
    let isLoginMode = true;
    let savedRecipes = [];
    let currentSavedRecipeId = null;
    let currentRecipeIndex = null;

    // 페이지 로드 시 로그인 상태 확인
    checkAuthStatus();

    async function checkAuthStatus() {
        try {
            const response = await fetch('/api/auth/me');
            const data = await response.json();

            if (data.user) {
                currentUser = data.user;
                updateAuthUI(true);
            } else {
                currentUser = null;
                updateAuthUI(false);
            }
        } catch (error) {
            console.error('Auth check failed:', error);
        }
    }

    function updateAuthUI(loggedIn) {
        if (loggedIn && currentUser) {
            loggedOutButtons.style.display = 'none';
            loggedInButtons.style.display = 'flex';
            userEmailSpan.textContent = currentUser.email;
        } else {
            loggedOutButtons.style.display = 'flex';
            loggedInButtons.style.display = 'none';
        }
    }

    // 로그인 버튼 클릭
    loginBtn.addEventListener('click', () => {
        isLoginMode = true;
        openAuthModal();
    });

    // 회원가입 버튼 클릭
    registerBtn.addEventListener('click', () => {
        isLoginMode = false;
        openAuthModal();
    });

    // 로그아웃 버튼 클릭
    logoutBtn.addEventListener('click', async () => {
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
            currentUser = null;
            updateAuthUI(false);
        } catch (error) {
            console.error('Logout failed:', error);
        }
    });

    // 인증 모달 열기
    function openAuthModal() {
        authEmail.value = '';
        authPassword.value = '';
        authError.style.display = 'none';

        if (isLoginMode) {
            authModalTitle.textContent = '로그인';
            authSubmitBtn.textContent = '로그인';
            authSwitchText.textContent = '계정이 없으신가요?';
            authSwitchBtn.textContent = '회원가입';
        } else {
            authModalTitle.textContent = '회원가입';
            authSubmitBtn.textContent = '회원가입';
            authSwitchText.textContent = '이미 계정이 있으신가요?';
            authSwitchBtn.textContent = '로그인';
        }

        authModal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function closeAuthModal() {
        authModal.style.display = 'none';
        document.body.style.overflow = '';
    }

    authModalClose.addEventListener('click', closeAuthModal);
    authModal.addEventListener('click', (e) => {
        if (e.target === authModal) closeAuthModal();
    });

    // 로그인/회원가입 전환
    authSwitchBtn.addEventListener('click', () => {
        isLoginMode = !isLoginMode;
        openAuthModal();
    });

    // 인증 폼 제출
    authForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const email = authEmail.value.trim();
        const password = authPassword.value;

        const endpoint = isLoginMode ? '/api/auth/login' : '/api/auth/register';

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });

            const data = await response.json();

            if (response.ok) {
                currentUser = data.user;
                updateAuthUI(true);
                closeAuthModal();
            } else {
                authError.textContent = data.error || '오류가 발생했습니다.';
                authError.style.display = 'block';
            }
        } catch (error) {
            authError.textContent = '네트워크 오류가 발생했습니다.';
            authError.style.display = 'block';
        }
    });

    // ============ 마이페이지 관련 ============

    mypageBtn.addEventListener('click', openMypageModal);

    async function openMypageModal() {
        profileEmail.textContent = currentUser.email;

        // 식이 제한 체크박스 설정
        const checkboxes = dietaryOptions.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(checkbox => {
            checkbox.checked = currentUser.dietary_restrictions?.includes(checkbox.value) || false;
        });

        // 저장된 레시피 로드
        await loadSavedRecipes();

        mypageModal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function closeMypageModal() {
        mypageModal.style.display = 'none';
        document.body.style.overflow = '';
    }

    mypageModalClose.addEventListener('click', closeMypageModal);
    mypageModal.addEventListener('click', (e) => {
        if (e.target === mypageModal) closeMypageModal();
    });

    // 식이 제한 저장
    saveDietaryBtn.addEventListener('click', async () => {
        const checkboxes = dietaryOptions.querySelectorAll('input[type="checkbox"]:checked');
        const restrictions = Array.from(checkboxes).map(cb => cb.value);

        try {
            const response = await fetch('/api/profile', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dietary_restrictions: restrictions })
            });

            if (response.ok) {
                currentUser.dietary_restrictions = restrictions;
                alert('식이 제한 설정이 저장되었습니다.');
            }
        } catch (error) {
            alert('저장에 실패했습니다.');
        }
    });

    // 저장된 레시피 로드
    async function loadSavedRecipes() {
        try {
            const response = await fetch('/api/recipes');
            const data = await response.json();

            if (response.ok) {
                savedRecipes = data.recipes;
                renderSavedRecipes();
            }
        } catch (error) {
            console.error('Failed to load recipes:', error);
        }
    }

    function renderSavedRecipes() {
        if (savedRecipes.length === 0) {
            savedRecipesList.innerHTML = '<p class="empty-message">저장된 레시피가 없습니다.</p>';
            return;
        }

        savedRecipesList.innerHTML = '';
        savedRecipes.forEach((recipe, index) => {
            const item = document.createElement('div');
            item.className = 'saved-recipe-item';

            const stars = recipe.rating > 0 ? '★'.repeat(recipe.rating) + '☆'.repeat(5 - recipe.rating) : '';

            item.innerHTML = `
                <div class="saved-recipe-info">
                    <h4>${recipe.recipe_name}</h4>
                    <span class="meta">${recipe.created_at.split('T')[0]}</span>
                </div>
                <div class="saved-recipe-rating">${stars}</div>
            `;

            item.addEventListener('click', () => openSavedRecipeModal(index));
            savedRecipesList.appendChild(item);
        });
    }

    // ============ 저장된 레시피 상세 ============

    function openSavedRecipeModal(index) {
        const recipe = savedRecipes[index];
        const recipeData = recipe.recipe_data;
        currentSavedRecipeId = recipe.id;

        document.getElementById('savedRecipeName').textContent = recipeData.name;
        document.getElementById('savedRecipeDifficulty').textContent = recipeData.difficulty || '보통';
        document.getElementById('savedRecipeDifficulty').className = 'badge ' + getDifficultyClass(recipeData.difficulty);
        document.getElementById('savedRecipeTime').textContent = recipeData.time || '30분';
        document.getElementById('savedRecipeServings').textContent = recipeData.servings || '2인분';

        // 재료
        const ingredientsList = document.getElementById('savedRecipeIngredients');
        ingredientsList.innerHTML = '';
        if (recipeData.ingredients) {
            recipeData.ingredients.forEach(ing => {
                const li = document.createElement('li');
                li.textContent = typeof ing === 'object' ? `${ing.name} ${ing.amount || ''}` : ing;
                ingredientsList.appendChild(li);
            });
        }

        // 조리 순서
        const stepsList = document.getElementById('savedRecipeSteps');
        stepsList.innerHTML = '';
        if (recipeData.steps) {
            recipeData.steps.forEach(step => {
                const li = document.createElement('li');
                li.textContent = step.replace(/^\d+\.\s*/, '');
                stepsList.appendChild(li);
            });
        }

        // 별점
        updateStarRating(recipe.rating || 0);

        // 메모
        recipeNotes.value = recipe.notes || '';

        savedRecipeModal.style.display = 'flex';
    }

    function closeSavedRecipeModal() {
        savedRecipeModal.style.display = 'none';
        currentSavedRecipeId = null;
    }

    savedRecipeModalClose.addEventListener('click', closeSavedRecipeModal);
    savedRecipeModal.addEventListener('click', (e) => {
        if (e.target === savedRecipeModal) closeSavedRecipeModal();
    });

    // 별점 클릭
    let currentRating = 0;
    ratingInput.querySelectorAll('.star').forEach(star => {
        star.addEventListener('click', () => {
            currentRating = parseInt(star.dataset.rating);
            updateStarRating(currentRating);
        });
    });

    function updateStarRating(rating) {
        currentRating = rating;
        ratingInput.querySelectorAll('.star').forEach((star, index) => {
            star.classList.toggle('active', index < rating);
        });
    }

    // 메모 저장
    saveNotesBtn.addEventListener('click', async () => {
        try {
            const response = await fetch(`/api/recipes/${currentSavedRecipeId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    rating: currentRating,
                    notes: recipeNotes.value
                })
            });

            if (response.ok) {
                alert('저장되었습니다.');
                await loadSavedRecipes();
            }
        } catch (error) {
            alert('저장에 실패했습니다.');
        }
    });

    // 레시피 삭제
    deleteRecipeBtn.addEventListener('click', async () => {
        if (!confirm('정말 삭제하시겠습니까?')) return;

        try {
            const response = await fetch(`/api/recipes/${currentSavedRecipeId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                closeSavedRecipeModal();
                await loadSavedRecipes();
            }
        } catch (error) {
            alert('삭제에 실패했습니다.');
        }
    });

    // ============ 레시피 저장 (추천 레시피에서) ============

    saveRecipeBtn.addEventListener('click', async () => {
        if (!currentUser) {
            alert('로그인이 필요합니다.');
            closeModal();
            isLoginMode = true;
            openAuthModal();
            return;
        }

        if (currentRecipeIndex === null) return;

        const recipe = recipes[currentRecipeIndex];

        try {
            const response = await fetch('/api/recipes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ recipe_data: recipe })
            });

            if (response.ok) {
                alert('레시피가 저장되었습니다!');
                closeModal();
            } else {
                const data = await response.json();
                alert(data.error || '저장에 실패했습니다.');
            }
        } catch (error) {
            alert('네트워크 오류가 발생했습니다.');
        }
    });

    // 레시피 모달 열 때 인덱스 저장
    const originalOpenRecipeModal = openRecipeModal;
    openRecipeModal = function(index) {
        currentRecipeIndex = index;
        originalOpenRecipeModal(index);
    };
});
