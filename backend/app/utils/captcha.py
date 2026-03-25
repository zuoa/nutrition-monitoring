"""Simple CAPTCHA generator using PIL."""
import random
import string
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import base64


def generate_captcha(length: int = 4) -> tuple[str, str]:
    """Generate a CAPTCHA code and its base64 image.

    Returns:
        (code, base64_image_string)
    """
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

    # Create image
    width, height = 120, 40
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Add noise (dots)
    for _ in range(100):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        draw.point((x, y), fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))

    # Add noise (lines)
    for _ in range(5):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(150, 220), random.randint(150, 220), random.randint(150, 220)), width=1)

    # Draw text
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except:
            font = ImageFont.load_default()

    # Draw each character with slight offset and rotation
    for i, char in enumerate(code):
        x = 20 + i * 25
        y = random.randint(5, 10)
        color = (random.randint(0, 100), random.randint(0, 100), random.randint(0, 100))
        draw.text((x, y), char, font=font, fill=color)

    # Save to base64
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()

    return code, f"data:image/png;base64,{img_str}"
