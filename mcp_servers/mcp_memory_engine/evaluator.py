# app/evaluator.py
import os
import json
import time
import httpx
from pydantic import BaseModel
 
# Drive path configuration
DEFAULT_SESSION_FILE = "E:/app/data/session_state.json" if os.name == "nt" else "/app/data/session_state.json"
SESSION_STATE_FILE = os.getenv("SESSION_STATE_FILE_PATH", DEFAULT_SESSION_FILE)
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:8400")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-local-dev-key")
ACTIVE_EVAL_MODEL = os.getenv("EVAL_MODEL_NAME", "local")
 
class MoodEngineState(BaseModel):
    stress_level: int = 1  # Scale 1-5
    closeness_score: int = 3  # Scale 1-5
    active_trajectory: str = "neutral_logistical"
    last_updated: float = 0.0
 
def bootstrap_mood_state() -> MoodEngineState:
    """Loads mood state from local disk or initializes default state if absent."""
    if os.path.exists(SESSION_STATE_FILE):
        try:
            with open(SESSION_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return MoodEngineState(**data)
        except Exception:
            pass
    return MoodEngineState()
 
def write_mood_state_to_disk(state: MoodEngineState):
    """Persists state to the data volume."""
    try:
        os.makedirs(os.path.dirname(SESSION_STATE_FILE), exist_ok=True)
        with open(SESSION_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2))
    except Exception as e:
        print(f"[Error] Failed to persist mood state to disk: {e}")
 
async def evaluate_mood_trajectory(history: list, app_state, turn_timestamp: float = 0.0):
    """
    Analyzes conversation history using Gemma 4 / LiteLLM.
    Includes sub-second race condition prevention and json_object fallback.
    """
    current_last_updated = getattr(app_state.mood_state, "last_updated", 0.0)
    eval_time = turn_timestamp or time.time()
    
    if turn_timestamp and turn_timestamp < current_last_updated:
        print(f"[Info] Discarding stale mood evaluation from timestamp {turn_timestamp:.4f} (current state: {current_last_updated:.4f}).")
        return
 
    system_prompt = (
        "You are an analytical sub-agent for an AI companion named Lyra. "
        "Your task is to analyze the provided conversation history and output ONLY a JSON object evaluating the current interaction.\n"
        "Schema:\n"
        "{\n"
        '  "stress_level": <int 1-5>,\n'
        '  "closeness_score": <int 1-5>,\n'
        '  "active_trajectory": <"conflict_distancing" | "sensual_intimate" | "casual_banter" | "vulnerable_emotional" | "thoughtful_analytical" | "neutral_logistical">\n'
        "}"
    )
 
    formatted_history = ""
    for turn in history:
        formatted_history += f"{turn['speaker'].upper()}: {turn['text']}\n"
 
    user_prompt = f"Analyze the following conversation context:\n\n{formatted_history}"
 
    headers = {"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": ACTIVE_EVAL_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }
 
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(f"{LITELLM_URL}/v1/chat/completions", json=payload, headers=headers)
            
            # Fallback: if local LLM rejects response_format, retry without it
            if resp.status_code == 400 and "response_format" in resp.text:
                payload.pop("response_format", None)
                resp = await client.post(f"{LITELLM_URL}/v1/chat/completions", json=payload, headers=headers)
 
            resp.raise_for_status()
            res_json = resp.json()
            result = res_json["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Background mood evaluation call failed: {e}")
            return
 
    try:
        if "```json" in result:
            result = result.split("```json")[1].split("```")[0].strip()
        elif "```" in result:
            result = result.split("```")[1].split("```")[0].strip()
            
        data = json.loads(result.strip())
    except Exception as json_err:
        print(f"LLM returned invalid structural JSON layout: {result}. Error: {json_err}")
        return
 
    # Verify latest timestamp again before applying state changes
    current_last_updated = getattr(app_state.mood_state, "last_updated", 0.0)
    if turn_timestamp and turn_timestamp < current_last_updated:
        print(f"[Info] Discarding completed stale evaluation from timestamp {turn_timestamp:.4f}.")
        return
 
    new_state = MoodEngineState(
        stress_level=int(data.get("stress_level", 1)),
        closeness_score=int(data.get("closeness_score", 3)),
        active_trajectory=data.get("active_trajectory", "neutral_logistical"),
        last_updated=eval_time
    )
 
    app_state.mood_state = new_state
    write_mood_state_to_disk(new_state)
 
 
def get_excluded_tones_for_query(state: MoodEngineState) -> list[str]:
    """
    Deterministic rule engine mapping mood to Tier 5 excluded tone vectors.
    Strictly aligned with schema.py APPROVED_TONE_VECTORS.
    """
    exclusions = []
    
    if state.active_trajectory == "conflict_distancing":
        exclusions.extend(["playful_teasing", "sensual_intimate", "somber_vulnerable"])
        
    elif state.active_trajectory in ["thoughtful_analytical", "neutral_logistical"]:
        exclusions.append("sensual_intimate")
        
    elif state.stress_level >= 4:
        exclusions.extend(["playful_teasing", "energetic_collaborative"])
        
    elif state.closeness_score <= 2:
        exclusions.extend(["sensual_intimate", "somber_vulnerable"])
        
    return list(set(exclusions))
