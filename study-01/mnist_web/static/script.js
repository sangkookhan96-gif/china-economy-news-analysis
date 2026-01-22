// Created: 2026-01-14 09:30:00
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const clearBtn = document.getElementById('clearBtn');
const predictBtn = document.getElementById('predictBtn');
const resultDiv = document.getElementById('result');
const confidenceDiv = document.getElementById('confidence');
const probabilitiesDiv = document.getElementById('probabilities');

let isDrawing = false;
let lastX = 0;
let lastY = 0;

ctx.lineWidth = 20;
ctx.lineCap = 'round';
ctx.lineJoin = 'round';
ctx.strokeStyle = 'black';

initializeProbabilityBars();

canvas.addEventListener('mousedown', startDrawing);
canvas.addEventListener('mousemove', draw);
canvas.addEventListener('mouseup', stopDrawing);
canvas.addEventListener('mouseout', stopDrawing);

clearBtn.addEventListener('click', clearCanvas);
predictBtn.addEventListener('click', predictDigit);

function startDrawing(e) {
    isDrawing = true;
    const rect = canvas.getBoundingClientRect();
    lastX = e.clientX - rect.left;
    lastY = e.clientY - rect.top;
}

function draw(e) {
    if (!isDrawing) return;
    const rect = canvas.getBoundingClientRect();
    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;
    ctx.beginPath();
    ctx.moveTo(lastX, lastY);
    ctx.lineTo(currentX, currentY);
    ctx.stroke();
    lastX = currentX;
    lastY = currentY;
}

function stopDrawing() {
    isDrawing = false;
}

function clearCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    resultDiv.textContent = '?';
    confidenceDiv.textContent = '그림을 그려주세요';
    initializeProbabilityBars();
}

function initializeProbabilityBars() {
    probabilitiesDiv.innerHTML = '';
    for (let i = 0; i < 10; i++) {
        const probItem = document.createElement('div');
        probItem.className = 'prob-item';
        probItem.innerHTML = `
            <div class="prob-label">${i}</div>
            <div class="prob-bar-container">
                <div class="prob-bar" id="prob-${i}" style="width: 0%"></div>
            </div>
            <div class="prob-value" id="prob-val-${i}">0%</div>
        `;
        probabilitiesDiv.appendChild(probItem);
    }
}

async function predictDigit() {
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const pixels = imageData.data;
    let isEmpty = true;
    
    for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] > 0) {
            isEmpty = false;
            break;
        }
    }
    
    if (isEmpty) {
        alert('먼저 숫자를 그려주세요!');
        return;
    }
    
    predictBtn.disabled = true;
    predictBtn.textContent = '인식 중...';
    
    try {
        const imageDataURL = canvas.toDataURL('image/png');
        const response = await fetch('/predict', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({image: imageDataURL})
        });
        
        const data = await response.json();
        
        if (data.success) {
            resultDiv.textContent = data.prediction;
            confidenceDiv.textContent = `신뢰도: ${data.confidence.toFixed(2)}%`;
            updateProbabilityBars(data.probabilities, data.prediction);
        } else {
            alert('오류: ' + data.error);
        }
    } catch (error) {
        alert('서버 오류');
    } finally {
        predictBtn.disabled = false;
        predictBtn.textContent = '인식하기';
    }
}

function updateProbabilityBars(probabilities, prediction) {
    probabilities.forEach((prob, index) => {
        const percentage = (prob * 100).toFixed(1);
        const barElement = document.getElementById(`prob-${index}`);
        const valueElement = document.getElementById(`prob-val-${index}`);
        barElement.style.width = `${percentage}%`;
        valueElement.textContent = `${percentage}%`;
        if (index === prediction) {
            barElement.classList.add('top');
        } else {
            barElement.classList.remove('top');
        }
    });
}
