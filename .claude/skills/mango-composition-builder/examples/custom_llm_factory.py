from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class CustomLLMFactory:
    """
    Factory for instantiating custom LLM clients dynamically.
    Ensures backwards compatibility and dynamic configuration.
    """
    
    def __init__(self, default_model: str = "default-model-v1") -> None:
        self.default_model = default_model
        
    def build_client(self, config: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        """
        Builds the LLM client based on the provided configuration.
        """
        config = config or {}
        model_name = config.get("model_name", self.default_model)
        temperature = config.get("temperature", 0.7)
        
        logger.info(f"Building LLM client for model: {model_name} with temp: {temperature}")
        
        # Merge kwargs for backwards compatibility
        client_config = {
            "model": model_name,
            "temperature": temperature,
            **kwargs
        }
        
        # Instantiate and return the mock client
        return type("MockLLMClient", (), {"config": client_config})()
