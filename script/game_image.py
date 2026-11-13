"""Save the camera frame sent to game vision as a PNG for local inspection."""

import base64
import os
import tempfile
from pathlib import Path


def save_game_image(image_b64: str, root: Path) -> Path:
    """Atomically replace the last captured image, regardless of API success."""
    path = Path(os.getenv('GAME_CAPTURE_IMAGE_PATH', 'script/temp.png')).expanduser()
    if not path.is_absolute():
        path = root / path
    data = base64.b64decode(image_b64, validate=True)
    if not data:
        raise ValueError('摄像头图像内容为空')
    # Camera providers return JPEG; encode PNG instead of only changing the suffix.
    import cv2
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('摄像头图像无法解码')
    success, encoded = cv2.imencode('.png', frame)
    if not success:
        raise RuntimeError('PNG 图像编码失败')
    data = encoded.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.game-image-', delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'[LOG][GAME] 本次视觉图像已保存: {path}')
    return path
