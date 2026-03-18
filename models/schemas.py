from pydantic import BaseModel, Field
from typing import Optional


class AnalysisRequest(BaseModel):
    file_id: str = Field(..., description="已上传文件的ID")
    query: Optional[str] = Field(None, description="可选的自定义分析问题")


class ChunkResult(BaseModel):
    chunk_id: str
    text: str
    page: int
    score: float


class ElementExtraction(BaseModel):
    element: str = Field(..., description="要素名称")
    value: str = Field(..., description="提取的内容")
    confidence: float = Field(..., ge=0.0, le=1.0)
    source_chunks: list[str] = Field(default_factory=list)


class CriticFeedback(BaseModel):
    page: int
    issue: str
    suggestion: str
    severity: str = Field(..., pattern="^(low|medium|high)$")


class IterationResult(BaseModel):
    iteration: int
    elements: list[ElementExtraction]
    critic_feedback: list[CriticFeedback]
    overall_confidence: float
    summary: str


class AnalysisResponse(BaseModel):
    file_id: str
    iterations: list[IterationResult]
    final_summary: str
    total_pages: int


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    total_pages: int
    message: str
