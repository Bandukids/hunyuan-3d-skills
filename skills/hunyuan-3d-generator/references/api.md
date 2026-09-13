# Hunyuan 3D API reference

Verified: 2026-09-13. Prefer the selected provider's current official documentation if it changes.

The generation payloads and default output formats below apply to **Professional 3.1/3.0**. For HY-3D-Express, read [express.md](express.md); it shares TokenHub authentication, regional origins, task lifecycle, downloads and error handling, but uses a different generation option set and a default concurrency limit of one.

## Sources

- [TokenHub HY-3D guide](https://cloud.tencent.com/document/product/1823/137181) — submission, query, multi-view schema, results.
- [TokenHub API usage](https://cloud.tencent.com/document/product/1823/130078) — regional origins, Bearer authentication, model list.
- [TokenHub model catalog](https://cloud.tencent.com/document/product/1823/130051) — model capabilities.
- [Legacy AI3D examples](https://cloud.tencent.com/document/product/1804/126189) — legacy protocol and 3.1 mode restrictions.
- [Legacy professional generation](https://cloud.tencent.com/document/product/1804/123447) and [query](https://cloud.tencent.com/document/api/1804/123448).

## Provider routing

| Setting | TokenHub (default) | Legacy AI3D (explicit) |
| --- | --- | --- |
| CLI | `--provider tokenhub` | `--provider legacy` |
| Origin | `https://tokenhub.tencentmaas.com` | `https://api.ai3d.cloud.tencent.com` |
| Submit | POST /v1/api/3d/submit | POST /v1/ai3d/submit |
| Query | POST /v1/api/3d/query | POST /v1/ai3d/query |
| Authorization | Bearer followed by API key | Raw API key, without Bearer |
| Model | hy-3d-3.1 / hy-3d-3.0 | 3.1 / 3.0 |
| Query body | model + id | JobId |
| Status | queued, in_progress, completed, failed | WAIT, RUN, DONE, FAIL |
| Result array | data | ResultFile3Ds |

Never infer a provider from the shape of an API key. Do not try a different protocol after an uncertain submission.

TokenHub Singapore origin is `https://tokenhub-intl.tencentmaas.com`. The documented backup origins replace `.com` with `.cn` for the same region. Use only the region where the account's service was opened; origins are not interchangeable quota pools. Pass an origin only, without /v1:

```powershell
python scripts/hunyuan_3d.py check-auth --base-url https://tokenhub-intl.tencentmaas.com
```

Environment defaults: `TOKENHUB_BASE_URL` for TokenHub; `HUNYUAN_3D_BASE_URL` only for legacy. The client restricts authenticated requests to these documented origins and refuses redirects.

TokenHub credential precedence: TOKENHUB_API_KEY, HUNYUAN_3D_API_KEY, TENCENT_HUNYUAN_API_KEY. Legacy uses only the last two. Credentials are read from the process environment and are never stored by the client.

## TokenHub payloads

Live compatibility finding (2026-09-13): a submission with the documented
generate_type="normal" was explicitly rejected before a task ID, reporting that
GenerateType accepts Normal/LowPoly/Geometry/Sketch. Omitting generate_type then
successfully created a 3.1 image-generation task. The client therefore omits this
field for Normal and relies on the documented default. Other modes still follow
the documented lowercase values and have not been live-verified; do not claim they
are validated or automatically retry an ambiguous submission.


Minimal text submission:

```json
{"model":"hy-3d-3.1","prompt":"一只小猫","enable_pbr":true}
```

Query must repeat the actual model used to submit:

```json
{"model":"hy-3d-3.1","id":"TASK_ID"}
```

Local images use raw Base64 in `image_base64`, without a data-URL prefix. Remote images use a plain public HTTPS string in `image_url`. The client does not fetch remote reference images during dry-run; remote size, format and reachability remain unverified until the service processes them.

| Option | Wire field / behavior |
| --- | --- |
| --prompt | prompt; at most 1024 Unicode characters; Chinese positive description |
| --image | image_base64; JPEG, PNG or WebP |
| --image-url | image_url; public HTTPS |
| --enable-pbr | enable_pbr=true; omit for geometry |
| --face-count | face_count; 3000..1500000, API default 500000 |
| --generate-type | normal / low_poly / geometry / sketch |
| --polygon-type | polygon_type; triangle or quadrilateral; low_poly only |
| --result-format | result_format; stl / usdz / fbx only |

Omit result_format for the default OBJ + GLB group; geometry normally returns GLB. Do not send glb or obj as result_format. OBJ may arrive as a ZIP containing textures.

Normal creates geometry with textures; Geometry creates an untextured model. LowPoly ignores face_count. Sketch permits prompt plus image; the other modes require one source. For 3.1 the client accepts Normal/Geometry and rejects LowPoly/Sketch. The TokenHub guide explicitly excludes low_poly; the legacy model documentation also excludes Sketch for 3.1, while the current model catalog lists 3.1 text, image, eight-view and geometry capabilities. Keep this compatibility constraint until official documentation confirms broader 3.1 support.

## Image and multi-view limits

- TokenHub single image: 128..5000 pixels per side, inclusive. Local raw file limit: 6 MiB; remote image documentation: at most 8 MB.
- Multi-view: JPEG/PNG, strictly greater than 128 and less than 5000 pixels per side. Budget all local images (including the front image) together: at most 6 MiB raw and 8 MiB after Base64 encoding. Mixed or remote inputs also count toward the service's total limit; the offline client cannot verify remote byte sizes.
- The service documents MB/M notation without defining decimal vs binary units; the client uses MiB. When close to a boundary, reduce inputs below the limit.
- The standard-library client checks signatures and dimension headers, not full image decoding or visual suitability. Inspect reference images before real submission.

The front image remains in image_base64/image_url. Each additional item in multi_view_images is exactly:

```json
{"view_type":"left","view_image_base64":"RAW_BASE64_OR_PUBLIC_HTTPS_URL"}
```

The unusual view_image_base64 field also accepts an image URL according to the official schema. Do not substitute the similarly named fields from the separate texture-generation API.

Allowed additional view types: left, right, back, top, bottom, left_front, right_front. Each may appear once. CLI syntax is repeated `--view 'left=path-or-https-url'`; paths with spaces must be quoted. This client supports multi-view only through TokenHub and requires a main image without a text prompt. The eight-view workflow is intended for 3.1; the TokenHub guide lists the same parameter for both models without a separate 3.0 view limit.

## Task lifecycle and results

Submission returns id and usually status=queued, plus request_id. Query responses contain status and may omit id; retain the submitted ID yourself.

A completed TokenHub response contains:

```json
{
  "status": "completed",
  "request_id": "REQUEST_ID",
  "data": [
    {
      "type": "glb",
      "url": "https://example.com/model.glb",
      "preview_image_url": "https://example.com/preview.png"
    }
  ]
}
```

The CLI normalizes both protocols to WAIT/RUN/DONE/FAIL in its top-level output. The redacted response preserves provider field names. For legacy, a Response envelope and direct objects are accepted.

- generate submits once, prints the Job ID and recovery command immediately, then polls and downloads.
- submit prints the ID and exits without waiting.
- query can inspect once or use --wait --download. Preserve provider, model and base URL from the submission.
- --poll-interval defaults to 5 seconds and rejects smaller values. --wait-timeout defaults to 1800 seconds; a poll already in flight is separately bounded by --http-timeout (default 60).
- Task IDs last 24 hours. Download returned signed URLs promptly. A local timeout does not cancel a cloud task.
- Downloads go to output-dir / job-id, with temporary files renamed after successful transfer. Existing names receive numeric suffixes. A resumed download may create another local copy; it never creates another generation task.
- HTTPS downloads use no API Authorization header. Duplicate preview URLs are downloaded once. Preview failure is a warning; required model failures produce a nonzero exit while preserving completed downloads.
- Diagnostic output redacts image Base64, environment keys, key-shaped strings, and URL query strings. Do not persist an unredacted API response when diagnosing errors.
- Exit codes: 0 for successful command execution (submit/query may still report a running task); 2 for validation/API/generation/download failure; 130 for interruption.

## Error handling

Use request_id when available to locate failures. API error objects are recognized even on HTTP 200. Check HTTP 401 for provider/key/region mismatches; 403 for account permissions or model access; 429 for quota, concurrency or rate limits. TokenHub subaccounts may require QcloudTokenhubFullAccess, and keys belong to the account that created them.

There is no automatic submission retry. If a submit times out, returns invalid JSON, or has no ID, the task may still exist: inspect the provider's task records before considering another submission. For polling or download failures, use query on the known ID. A failed task returns an error even for query without --wait.

check-auth performs only GET /v1/models. It checks key acceptance and lists HY-3D entries in the returned catalog; it cannot prove generation entitlement, remaining credits, or generation quality.

## Legacy image compatibility

Legacy's default local encoding remains `ImageUrl: {Url: 'data:image/...;base64,...'}`. Explicit `--image-transport raw-base64` uses ImageBase64 instead. A public image URL remains ImageUrl.Url. Image dimensions retain the older strict 128 < side < 5000 rule.

Only if a legacy submit is explicitly rejected for ImageUrl.Url before any Job ID exists, consider the alternate raw-base64 transport. Never apply this fallback to TokenHub or after an ambiguous network failure. Confirm the rejected attempt and existing authorization before a reasoned retry; do not automatically retry or switch model/provider.
