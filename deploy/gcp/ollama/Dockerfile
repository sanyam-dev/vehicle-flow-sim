# Private Ollama server for the chat agent, run on Cloud Run with one L4 GPU.
# The model is pulled at build time so a cold start only loads it from the
# image instead of downloading ~5 GB.
FROM ollama/ollama:0.40.12

ARG MODEL=qwen3:8b
# Listen on Cloud Run's port; keep the model in memory for the instance's
# lifetime; let a few chats share the GPU.
ENV OLLAMA_HOST=0.0.0.0:8080 \
    OLLAMA_MODELS=/models \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_NUM_PARALLEL=4 \
    OLLAMA_DEBUG=false

RUN ollama serve & pid=$! \
 && until ollama list >/dev/null 2>&1; do sleep 1; done \
 && ollama pull "$MODEL" && kill $pid

ENTRYPOINT ["ollama", "serve"]
