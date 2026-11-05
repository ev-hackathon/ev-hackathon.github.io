#!/usr/bin/env bash
# GitHub Pages에서 '진행 중'으로 멈춘 배포 정리 (deploy.yml이 배포 실패 시 부름)
#
# 배경: 2026-11-05 04:34 배포가 GitHub 쪽에서 30분 넘게 '진행 중'으로 멈춰, 그동안 모든 새 배포가
#       "Please cancel <sha> first"로 거절되고 사이트가 04:30 데이터에 멈췄다.
#       수집기가 15분마다 부르는 배포는 모두 같은 커밋이라, 같은 커밋의 멈춘 배포도 정리해야 한다.
#       deploy.yml은 concurrency 그룹으로 한 번에 하나만 돌므로, 이 시점에 끝나지 않은 배포는 모두 멈춘 것이다.
#
# 사용:  GH_TOKEN=... GITHUB_REPOSITORY=owner/repo scripts/pages_unstick.sh [--dry-run | --check-permission]
#   (기본)              끝나지 않은 최근 Pages 배포를 모두 취소
#   --dry-run           상태만 출력
#   --check-permission  상태 출력 + 존재하지 않는 배포 취소를 시도해 권한 확인 (404=권한 있음, 403=권한 없음)
set -uo pipefail
mode="${1:-cancel}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY 필요}"
terminal="succeed deployment_cancelled deployment_failed deployment_content_failed deployment_lost deployment_attempt_error"

shas=$(gh api "repos/$repo/deployments?environment=github-pages&per_page=15" --jq '.[].sha' | awk '!seen[$0]++')
if [ -z "$shas" ]; then echo "::warning::Pages 배포 목록을 읽지 못함 (deployments: read 권한 확인)"; fi
stuck=0
for sha in $shas; do
  st=$(gh api "repos/$repo/pages/deployments/$sha" --jq .status 2>/dev/null) || st="(조회 실패)"
  echo "  ${sha:0:7}  $st"
  case " $terminal (조회 실패) " in *" $st "*) continue ;; esac
  stuck=$((stuck + 1))
  if [ "$mode" = "cancel" ]; then
    echo "  → 취소: ${sha:0:7} ($st)"
    gh api -X POST "repos/$repo/pages/deployments/$sha/cancel" >/dev/null && echo "    취소됨" || echo "::warning::${sha:0:7} 취소 실패"
  fi
done
echo "끝나지 않은 배포 ${stuck}건 ($mode)"

if [ "$mode" = "--check-permission" ]; then
  code=$(gh api -X POST "repos/$repo/pages/deployments/0000000000000000000000000000000000000000/cancel" 2>&1 | grep -o 'HTTP [0-9]*' | head -1)
  echo "취소 권한 확인 (없는 배포에 취소 시도): ${code:-응답 없음}  — 404면 권한 있음, 403이면 권한 없음"
fi
exit 0
