from app.inference_app import create_inference_app

app = create_inference_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
