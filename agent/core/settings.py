from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
    )
    OPENAI_API_KEY : str
    ANTHROPIC_API_KEY : str
    GEMINI_KEY : str
    
settings = Settings()