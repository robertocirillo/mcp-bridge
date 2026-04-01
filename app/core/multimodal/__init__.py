"""Multimodal query preparation and image resolution helpers."""

__all__ = [
    "BuiltModelQuery",
    "PDFInputCapability",
    "ImageInputCapability",
    "ModelQueryInput",
    "MultimodalInputValidationError",
    "PreparedModelQueryInput",
    "QueryAssetResolver",
    "QueryImageResolver",
    "RemoteImageFetchError",
    "RemoteImageFetcher",
    "ResolvedDocumentInput",
    "ResolvedImageInput",
    "ResolvedQueryInputPayload",
    "build_model_query",
    "describe_query_input",
    "ensure_pdf_input_supported",
    "ensure_image_input_supported",
    "extract_query_text",
    "has_query_pdf_input",
    "has_query_visual_input",
    "is_langchain_human_message",
    "replace_query_text",
    "resolve_pdf_input_capability",
    "resolve_image_input_capability",
    "resolve_request_query",
    "sanitize_multimodal_error",
    "summarize_query_input",
    "validate_multimodal_query_request",
]

_EXPORTS = {
    "BuiltModelQuery": (".model_query", "BuiltModelQuery"),
    "PDFInputCapability": (".capabilities", "PDFInputCapability"),
    "ImageInputCapability": (".capabilities", "ImageInputCapability"),
    "ModelQueryInput": (".model_query", "ModelQueryInput"),
    "MultimodalInputValidationError": (".validation", "MultimodalInputValidationError"),
    "PreparedModelQueryInput": (".model_query", "PreparedModelQueryInput"),
    "QueryAssetResolver": (".image_resolver", "QueryAssetResolver"),
    "QueryImageResolver": (".image_resolver", "QueryImageResolver"),
    "RemoteImageFetchError": (".image_fetch", "RemoteImageFetchError"),
    "RemoteImageFetcher": (".image_fetch", "RemoteImageFetcher"),
    "ResolvedDocumentInput": (".image_data", "ResolvedDocumentInput"),
    "ResolvedImageInput": (".image_data", "ResolvedImageInput"),
    "ResolvedQueryInputPayload": (".image_data", "ResolvedQueryInputPayload"),
    "build_model_query": (".model_query", "build_model_query"),
    "describe_query_input": (".model_query", "describe_query_input"),
    "ensure_pdf_input_supported": (".capabilities", "ensure_pdf_input_supported"),
    "ensure_image_input_supported": (".capabilities", "ensure_image_input_supported"),
    "extract_query_text": (".model_query", "extract_query_text"),
    "has_query_pdf_input": (".model_query", "has_query_pdf_input"),
    "has_query_visual_input": (".model_query", "has_query_visual_input"),
    "is_langchain_human_message": (".model_query", "is_langchain_human_message"),
    "replace_query_text": (".model_query", "replace_query_text"),
    "resolve_pdf_input_capability": (".capabilities", "resolve_pdf_input_capability"),
    "resolve_image_input_capability": (".capabilities", "resolve_image_input_capability"),
    "resolve_request_query": (".model_query", "resolve_request_query"),
    "sanitize_multimodal_error": (".model_query", "sanitize_multimodal_error"),
    "summarize_query_input": (".model_query", "summarize_query_input"),
    "validate_multimodal_query_request": (".preflight", "validate_multimodal_query_request"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'app.core.multimodal' has no attribute {name!r}") from exc

    from importlib import import_module

    module = import_module(module_name, __name__)
    return getattr(module, attr_name)
