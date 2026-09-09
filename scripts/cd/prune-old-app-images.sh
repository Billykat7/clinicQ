#!/usr/bin/env bash
#
# Remove local app images whose semver tag is strictly older than the deployed tag.
# Keeps images currently used by running containers (any tag/digest still in use).
#
# Intended to run on the production host after a successful:
#   docker compose ... up -d --pull always
#
# Usage:
#   prune-old-app-images.sh <image_ref>
#   prune-old-app-images.sh ghcr.io/btktechnologies/properties:v3.0.0
#   prune-old-app-images.sh --dry-run ghcr.io/btktechnologies/properties:v3.0.0
#
# Only tags matching vMAJOR.MINOR.PATCH (optional leading v) on the same repository
# are considered. Non-semver tags (latest, sha-*, <none>) are left alone.
#
# Requires: docker, bash, sort (coreutils with -V). Compatible with bash 3.2+.

set -euo pipefail

DRY_RUN=0
IMAGE_REF=""

usage() {
  cat <<'EOF'
Usage: prune-old-app-images.sh [--dry-run] <image_ref>

  image_ref   Full image reference including tag, e.g. ghcr.io/org/repo:v3.0.0
  --dry-run   Print what would be removed without calling docker rmi

Removes local images for the same repository with semver tags strictly older
than the deployed tag. Skips any image ID still referenced by a running container.
EOF
}

# Return 0 if $1 is a semver tag (optional leading v), e.g. v1.2.3 or 1.2.3.
is_semver_tag() {
  local tag="$1"
  [[ "$tag" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# Strip optional leading v for version sort / compare.
semver_numeric() {
  local tag="$1"
  echo "${tag#v}"
}

# Return 0 if version $1 is strictly less than version $2 (semver, no leading v).
version_lt() {
  local left="$1"
  local right="$2"
  if [[ "$left" == "$right" ]]; then
    return 1
  fi
  local first
  first="$(printf '%s\n%s\n' "$left" "$right" | sort -V | head -n1)"
  [[ "$first" == "$left" ]]
}

# Print unique image IDs used by running containers (full sha256:...).
running_image_ids() {
  local ids
  ids="$(docker ps -q 2>/dev/null || true)"
  if [[ -z "$ids" ]]; then
    return 0
  fi
  # shellcheck disable=SC2086
  docker inspect --format '{{.Image}}' $ids 2>/dev/null | sort -u || true
}

# Return 0 if image ID $1 appears in newline-separated list $2.
id_in_list() {
  local want="$1"
  local list="$2"
  [[ -n "$want" ]] || return 1
  printf '%s\n' "$list" | grep -qxF "$want"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$IMAGE_REF" ]]; then
        echo "error: unexpected argument: $1" >&2
        usage >&2
        exit 2
      fi
      IMAGE_REF="$1"
      shift
      ;;
  esac
done

if [[ -z "$IMAGE_REF" ]]; then
  echo "error: image_ref is required" >&2
  usage >&2
  exit 2
fi

if [[ "$IMAGE_REF" != *:* ]]; then
  echo "error: image_ref must include a tag (repo:tag), got: $IMAGE_REF" >&2
  exit 2
fi

# If someone passed repo@sha256:..., refuse — we need a semver deploy tag.
if [[ "$IMAGE_REF" == *@* ]]; then
  echo "error: image_ref must be repository:semver-tag, not a digest ref: $IMAGE_REF" >&2
  exit 2
fi

REPO="${IMAGE_REF%:*}"
DEPLOY_TAG="${IMAGE_REF##*:}"

if ! is_semver_tag "$DEPLOY_TAG"; then
  echo "error: deploy tag is not semver (vMAJOR.MINOR.PATCH): $DEPLOY_TAG" >&2
  exit 2
fi

DEPLOY_VER="$(semver_numeric "$DEPLOY_TAG")"

echo "Pruning local images for ${REPO} older than ${DEPLOY_TAG} (semver < ${DEPLOY_VER})"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run: no images will be removed"
fi

RUNNING_IDS="$(running_image_ids)"

removed=0
skipped_running=0
kept_newer_or_equal=0
skipped_nonsemver=0

# Use --no-trunc so IDs match docker inspect {{.Image}} for the "in use" check.
while IFS=$'\t' read -r repo tag id; do
  [[ -n "${repo:-}" ]] || continue
  if [[ "$repo" != "$REPO" ]]; then
    continue
  fi
  if [[ "$tag" == "<none>" ]] || ! is_semver_tag "$tag"; then
    skipped_nonsemver=$((skipped_nonsemver + 1))
    continue
  fi

  ver="$(semver_numeric "$tag")"
  if ! version_lt "$ver" "$DEPLOY_VER"; then
    kept_newer_or_equal=$((kept_newer_or_equal + 1))
    continue
  fi

  ref="${repo}:${tag}"
  if id_in_list "$id" "$RUNNING_IDS"; then
    echo "skip (in use by running container): ${ref} (${id})"
    skipped_running=$((skipped_running + 1))
    continue
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "would remove: ${ref} (${id})"
    removed=$((removed + 1))
    continue
  fi

  echo "removing: ${ref} (${id})"
  if docker rmi "$ref"; then
    removed=$((removed + 1))
  else
    echo "warning: failed to remove ${ref}; continuing" >&2
  fi
done < <(docker images --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}' "$REPO" 2>/dev/null || true)

# Drop dangling layers left behind after tagged rmi (does not remove tagged images).
if [[ "$DRY_RUN" -eq 0 ]]; then
  docker image prune -f >/dev/null || true
fi

echo "Done. removed=${removed} skipped_running=${skipped_running} kept_ge_deploy=${kept_newer_or_equal} skipped_nonsemver=${skipped_nonsemver}"
