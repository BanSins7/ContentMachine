"""Default example pipelines. Model references must be explicitly configured."""
from __future__ import annotations

def strong_only(model_ref: str = "strong_llm") -> dict:
    return {"stages":[{"id":"classify","type":"llm_classifier","enabled":True,
                        "model_ref":model_ref,"run_when":"always"}]}

def cascade(embedding_ref: str = "embedding_model",
            fast_ref: str = "fast_llm", strong_ref: str = "strong_llm") -> dict:
    return {"stages":[
      {"id":"retrieve","type":"embedding_retrieval","enabled":True,
       "model_ref":embedding_ref,
       "settings":{"top_k":5,"allow_candidate_drop":False}},
      {"id":"fast","type":"llm_classifier","enabled":True,
       "model_ref":fast_ref,"run_when":"always"},
      {"id":"resolve","type":"llm_classifier","enabled":True,
       "model_ref":strong_ref,"run_when":"on_uncertain"}]}
