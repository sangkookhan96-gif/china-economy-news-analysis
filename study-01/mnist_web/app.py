


# Created: 2026-01-14 09:30:00
from flask import Flask, render_template, request, jsonify
import torch
import torch.nn as nn
from PIL import Image
import io
import base64
import numpy as np

app = Flask(__name__)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class CNN(nn.Module):
    def __init__(self):
        super(CNN, self).__init__()
        self.conv1 = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2)
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2)
        )
        self.fc1 = nn.Linear(64 * 7 * 7, 128)
        self.fc2 = nn.Linear(128, 10)
        self.dropout = nn.Dropout(0.5)
    
    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = x.view(x.size(0), -1)
        x = torch.relu(self.fc1(x))
        x = self.dropout(x)
        x = self.fc2(x)
        return x

model = CNN().to(device)
try:
    model.load_state_dict(torch.load('mnist_cnn.pth', map_location=device))
    model.eval()
    print("Model loaded successfully!")
except:
    print("Warning: Could not load model.")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.get_json()
        image_data = data['image'].split(',')[1]
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes)).convert('L')
        image = image.resize((28, 28), Image.LANCZOS)
        image_array = np.array(image).astype('float32') / 255.0
        image_array = 1.0 - image_array
        image_array = (image_array - 0.1307) / 0.3081
        image_tensor = torch.FloatTensor(image_array).unsqueeze(0).unsqueeze(0).to(device)
        
        with torch.no_grad():
            output = model(image_tensor)
            probabilities = torch.softmax(output, dim=1)
            prediction = output.argmax(dim=1).item()
            confidence = probabilities[0][prediction].item() * 100
        
        all_probs = probabilities[0].cpu().numpy().tolist()
        
        return jsonify({
            'success': True,
            'prediction': int(prediction),
            'confidence': float(confidence),
            'probabilities': all_probs
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("Starting MNIST Web Application...")
    print("Open: http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
