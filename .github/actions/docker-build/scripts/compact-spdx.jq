# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Only document-local file identifiers change, never file or package metadata.
def reference($ids): if type == "string" then $ids[.] // . else . end;
def references($ids): map(reference($ids));

if .spdxVersion != "SPDX-2.3" or .SPDXID != "SPDXRef-DOCUMENT" then
  error("Expected an SPDX 2.3 document")
else . end
| . as $original
| [.files[]?.SPDXID] as $files
| [.SPDXID, .packages[]?.SPDXID, .snippets[]?.SPDXID] as $other_ids
| if ($files | length) != ($files | unique | length) then
    error("Duplicate file SPDX identifiers")
  else . end
| if any($files[]; . as $id | $other_ids | index($id)) then
    error("File identifier duplicates another SPDX element")
  else . end
| ($files | to_entries | map({key: .value, value: ("SPDXRef-f" + (.key | tostring))}) | from_entries) as $ids
| if any($ids[]; . as $id | $other_ids | index($id)) then
    error("Compact file identifier collides with another SPDX element")
  else . end
| if has("files") then .files |= map(
    .SPDXID |= reference($ids)
    | if has("fileDependencies") then .fileDependencies |= references($ids) else . end
  ) else . end
| if has("packages") then .packages |= map(
    if has("hasFiles") then .hasFiles |= references($ids) else . end
  ) else . end
| if has("documentDescribes") then .documentDescribes |= references($ids) else . end
| if has("relationships") then .relationships |= map(
    .spdxElementId |= reference($ids)
    | .relatedSpdxElement |= reference($ids)
  ) else . end
| if has("snippets") then .snippets |= map(
    .snippetFromFile |= reference($ids)
    | .ranges |= map(
        .startPointer.reference |= reference($ids)
        | .endPointer.reference |= reference($ids)
      )
  ) else . end
| .documentNamespace += "/dsx-file-ids-v1"
| {
    document: .,
    idMap: {
      originalNamespace: $original.documentNamespace,
      compactNamespace: .documentNamespace,
      fileIds: ($ids | to_entries | map({key: .value, value: .key}) | from_entries)
    }
  }
