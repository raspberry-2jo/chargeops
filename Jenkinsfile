def notifyDelivery(String event) {
  if (env.PUBLISH_IMAGES != 'true') { return }
  try {
    withEnv(["NOTIFY_EVENT=${event}"]) {
      withCredentials([string(credentialsId: params.SLACK_CREDENTIAL_ID.trim(), variable: 'SLACK_CICD_TOKEN')]) {
        sh(script: 'python3.12 scripts/notify_ci.py', returnStatus: true)
      }
    }
  } catch (Exception ignored) {
    echo 'Slack notification unavailable; check the configured credential. Delivery result is unchanged.'
  }
}

def githubStatus(String state, String context = 'chargeops/dev-integration') {
  withEnv(["GITHUB_STATUS_STATE=${state}", "GITHUB_STATUS_CONTEXT=${context}"]) {
    withCredentials([string(credentialsId: params.GITHUB_PR_CREDENTIAL_ID.trim(), variable: 'GITHUB_PR_TOKEN')]) {
      sh '.ci-venv/bin/python scripts/github_cd.py status --source "$SOURCE_COMMIT" --state "$GITHUB_STATUS_STATE" --context "$GITHUB_STATUS_CONTEXT"'
    }
  }
}

pipeline {
  agent { label 'mgmt-agent' }
  options {
    timestamps()
    disableConcurrentBuilds()
    skipDefaultCheckout(true)
    timeout(time: 120, unit: 'MINUTES')
  }
  // Single delivery job: */kim_chanju; wait for PR approval+merge in this build.
  triggers { pollSCM('H/5 * * * *') }
  parameters {
    booleanParam(name: 'BUILD_ALL_IMAGES', defaultValue: false,
      description: 'Build all six images. The first build automatically builds all images.')
    booleanParam(name: 'ENABLE_ENVIRONMENT_CD', defaultValue: true,
      description: 'Verify CSMS in Dev, wait for source approval+merge, then deploy built app digests to ops; only CSMS uses Canary.')
    booleanParam(name: 'ENABLE_CANARY', defaultValue: true,
      description: 'After approved PR merge, promote the same Dev-verified CSMS digest through Ops 25/50/100 percent Canary with Git recovery.')
    choice(name: 'DEV_DELIVERY_MODE', choices: ['auto', 'rolling', 'deployment', 'canary'],
      description: 'Auto requires the one-time Dev RollingUpdate handover. deployment/canary are explicit legacy compatibility modes.')
    booleanParam(name: 'RUN_OCPP_FIXTURE_TEST', defaultValue: false,
      description: 'Optional: OCPP checks WRITE to the existing DB using a pre-approved CI-DEV charger. Default verification is read-only.')
    booleanParam(name: 'PUBLISH_DEV_TAG', defaultValue: false,
      description: 'Enable only AFTER ops digest freeze and DEV-only Image Updater bootstrap. Unique build tags are always published on the publish branch.')
    string(name: 'GITOPS_CREDENTIAL_ID', defaultValue: 'chargeops-gitops-write',
      description: 'Jenkins username/password credential for GitOps Git access.')
    string(name: 'DEV_KUBECONFIG_CREDENTIAL_ID', defaultValue: 'chargeops-dev-kubeconfig',
      description: 'Jenkins secret-file kubeconfig limited to dev verification and chargeops-dev Application reads.')
    string(name: 'CANARY_KUBECONFIG_CREDENTIAL_ID', defaultValue: 'chargeops-canary-kubeconfig',
      description: 'Restricted Secret file from bootstrap-canary.py: ops rollout observation, abort and the existing ops Application Sync.')
    string(name: 'GITHUB_PR_CREDENTIAL_ID', defaultValue: 'chargeops-github-pr-token',
      description: 'Secret text: chargeops app Pull requests write, Commit statuses write and Contents read.')
    string(name: 'SLACK_CREDENTIAL_ID', defaultValue: 'chargeops-cicd-slack-token',
      description: 'Secret text: Slack bot token with chat:write; invite the bot to the channel.')
    string(name: 'SLACK_CHANNEL_ID', defaultValue: 'C0CBS4DFPGW', description: 'CI/CD notification channel ID.')
    string(name: 'DEV_ARGO_APPLICATION', defaultValue: 'chargeops-dev',
      description: 'Existing Argo CD Application targeting namespace dev and the selected Dev overlay.')
    string(name: 'OPS_ARGO_APPLICATION', defaultValue: 'chargeops-chatops-dev',
      description: 'Existing manual Argo CD Application targeting namespace ops and overlays/canary.')
    booleanParam(name: 'ECR_MIRROR', defaultValue: true,
      description: 'Also push published images to AWS ECR (copy only; deployment still uses REGISTRY).')
    string(name: 'ECR_CREDENTIAL_ID', defaultValue: 'chargeops-ecr-push',
      description: 'Username/password: AWS access key ID / secret access key (ECR push only).')
    string(name: 'ECR_REGISTRY_CREDENTIAL_ID', defaultValue: 'chargeops-ecr-registry',
      description: 'Secret text: <account>.dkr.ecr.ap-northeast-2.amazonaws.com/chargeops')
  }
  environment {
    REGISTRY = 'registry.chargeops.lab:5000/chargeops'
    APP_ROOT = 'apps'
    PUBLISH_BRANCH = 'kim_chanju'
    PRODUCTION_BRANCH = 'dev'
    SLACK_CICD_CHANNEL = "${params.SLACK_CHANNEL_ID}"
    DEV_ARGO_APPLICATION = "${params.DEV_ARGO_APPLICATION}"
    OPS_ARGO_APPLICATION = "${params.OPS_ARGO_APPLICATION}"
  }
  stages {
    stage('Checkout') {
      steps {
        script {
          env.DEV_CHECK_STATE = 'not_started'
          env.DEV_GITOPS_OVERLAY = params.DEV_DELIVERY_MODE == 'canary' ? 'chargeops-chatops/overlays/development-canary' :
            (params.DEV_DELIVERY_MODE == 'deployment' ? 'chargeops-chatops/overlays/development' : 'chargeops-chatops/overlays/development-rolling')
          def checkoutVars = checkout scm
          env.CHECKOUT_BRANCH = (env.BRANCH_NAME ?: checkoutVars.GIT_BRANCH ?: env.GIT_BRANCH ?: '')
            .replaceFirst(/^refs\/heads\//, '').replaceFirst(/^origin\//, '')
          env.PUBLISH_IMAGES = (env.CHECKOUT_BRANCH == env.PUBLISH_BRANCH && !env.CHANGE_ID).toString()
          env.SOURCE_COMMIT = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
          def sha = sh(script: 'git rev-parse --short=12 HEAD', returnStdout: true).trim()
          def tagBranch = env.CHECKOUT_BRANCH == env.PRODUCTION_BRANCH ? 'dev' : 'kim_chanju'
          env.IMAGE_TAG = 'build-' + tagBranch + '-' + env.BUILD_NUMBER + '-' + sha
          echo 'IMAGE_TAG=' + env.IMAGE_TAG
          echo 'Branch=' + env.CHECKOUT_BRANCH + ', publish images=' + env.PUBLISH_IMAGES
          notifyDelivery('CI started')
        }
      }
    }
    stage('Check agent tools') {
      steps {
        sh '''
          set -eu
          command -v python3.12
          python3.12 -m venv --help >/dev/null
          command -v docker
          docker info >/dev/null
        '''
      }
    }
    stage('Detect changed images') {
      steps {
        script {
          def diffOutput = sh(script: '''
            set -eu
            CURRENT_COMMIT="$(git rev-parse HEAD)"
            BASE="${GIT_PREVIOUS_SUCCESSFUL_COMMIT:-}"
            if [ -z "$BASE" ] || ! BASE="$(git rev-parse --verify --end-of-options "${BASE}^{commit}" 2>/dev/null)"; then
              printf '%s\n' '__BUILD_ALL__'
            else
              git diff --name-only --no-renames "$BASE" "$CURRENT_COMMIT" -- \
                "$APP_ROOT/" Jenkinsfile requirements-dev.txt pytest.ini scripts/ tests/
            fi
          ''', returnStdout: true).trim()
          def changedFiles = diffOutput ? diffOutput.readLines().findAll { it } : []
          def appPaths = [
            'ai-gateway': 'chargeops-ai-gateway',
            'chatops-bot': 'chargeops-chatops-bot',
            'csms': 'chargeops-csms',
            'notifier': 'chargeops-notifier',
            'virtual-charger': 'chargeops-vcharger',
            'collector': 'chargeops-collector'
          ]
          def allImages = appPaths.values().toList()
          def selected = new LinkedHashSet()
          def runTests = false
          if (params.BUILD_ALL_IMAGES || changedFiles.contains('__BUILD_ALL__')) {
            selected.addAll(allImages)
            runTests = true
            echo 'First build or BUILD_ALL_IMAGES=true: building all six images.'
          } else {
            changedFiles.each { path ->
              if (path == 'Jenkinsfile' || path == 'scripts/ci.py' || path == 'scripts/trivy_gate.py' || path == 'scripts/trivy_exceptions.py' || path.startsWith('security/') || path == env.APP_ROOT + '/.dockerignore') {
                selected.addAll(allImages)
              } else if (path == 'requirements-dev.txt' || path == 'pytest.ini' || path.startsWith('scripts/') ||
                         path.startsWith('tests/') || path.contains('/tests/') || path.contains('/test_')) {
                runTests = true
                echo 'Test-only change: ' + path
              } else if (path.endsWith('.md') || path.endsWith('.sql') || path.endsWith('/.gitkeep')) {
                echo 'Documentation/query-only change: ' + path
              } else if (path.startsWith(env.APP_ROOT + '/common/')) {
                selected.addAll(['chargeops-ai-gateway', 'chargeops-chatops-bot', 'chargeops-notifier'])
              } else if (path.startsWith(env.APP_ROOT + '/')) {
                def matched = appPaths.find { folder, image -> path.startsWith(env.APP_ROOT + '/' + folder + '/') }
                if (matched) { selected.add(matched.value) }
                else { selected.addAll(allImages) }
              }
            }
          }
          // CSMS remains the Dev verification anchor even for a notifier-only release.
          if (params.ENABLE_ENVIRONMENT_CD && env.PUBLISH_IMAGES == 'true') {
            selected.add('chargeops-csms')
          }
          env.APP_CHANGED = (runTests || !selected.isEmpty()).toString()
          env.BUILD_IMAGES = selected.join(' ')
          echo 'Selected images: ' + (env.BUILD_IMAGES ?: '(none)')
        }
      }
    }
    stage('Test (Python 3.12)') {
      when { expression { env.APP_CHANGED == 'true' || env.PUBLISH_IMAGES == 'true' } }
      steps {
        sh '''
          set -eu
          python3.12 -m venv .ci-venv
          .ci-venv/bin/python -m pip install --upgrade pip
          .ci-venv/bin/python -m pip install -r requirements-dev.txt
          .ci-venv/bin/python scripts/env_cd.py reset
          .ci-venv/bin/python -m pytest -q tests/
          python3.12 scripts/ci.py validate
        '''
      }
    }
    stage('Validate Dev RollingUpdate handover') {
      when { expression { params.ENABLE_ENVIRONMENT_CD && params.DEV_DELIVERY_MODE == 'auto' && env.PUBLISH_IMAGES == 'true' &&
        (env.BUILD_IMAGES ?: '').tokenize().contains('chargeops-csms') } }
      steps {
        withCredentials([file(credentialsId: params.DEV_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG')]) {
          script {
            def path = sh(script: '.ci-venv/bin/python scripts/env_cd.py dev-overlay', returnStdout: true).trim()
            if (path != 'chargeops-chatops/overlays/development-rolling') {
              error('Complete migrate-dev-rolling.py on mgmt before automatic delivery; Dev and Ops images unchanged.')
            }
          }
        }
      }
    }
    stage('Docker Build: changed images only') {
      when { expression { env.BUILD_IMAGES?.trim() } }
      steps {
        sh '''
          set -eu
          if [ "$PUBLISH_IMAGES" = 'true' ]; then
            if [ "$CHECKOUT_BRANCH" != "$PUBLISH_BRANCH" ] || [ -n "${CHANGE_ID:-}" ]; then
              echo 'Image publication requires the configured non-PR branch.' >&2
              exit 1
            fi
          fi
          # Reject removed or unknown names before running Docker.
          for NAME in $BUILD_IMAGES; do
            case "$NAME" in
              chargeops-ai-gateway|chargeops-chatops-bot|chargeops-csms|chargeops-notifier|chargeops-vcharger|chargeops-collector) ;;
              *) echo "Unknown image: $NAME" >&2; exit 1 ;;
            esac
          done
          build_if_selected() {
            NAME="$1"
            DOCKERFILE="$2"
            CONTEXT="$3"
            case " $BUILD_IMAGES " in
              *" $NAME "*)
                docker build --pull -f "$DOCKERFILE" \
                  -t "$REGISTRY/$NAME:$IMAGE_TAG" "$CONTEXT"
                ;;
            esac
          }
          build_if_selected chargeops-ai-gateway apps/ai-gateway/Dockerfile apps
          build_if_selected chargeops-chatops-bot apps/chatops-bot/Dockerfile apps
          build_if_selected chargeops-csms apps/csms/Dockerfile apps
          build_if_selected chargeops-notifier apps/notifier/Dockerfile apps
          build_if_selected chargeops-vcharger apps/virtual-charger/Dockerfile apps
          build_if_selected chargeops-collector apps/collector/Dockerfile apps
        '''
      }
    }
    stage('Trivy Quality Gate') {
      when { expression { env.BUILD_IMAGES?.trim() } }
      steps {
        timeout(time: 30, unit: 'MINUTES') {
          sh '''
            set -eu
            python3.12 scripts/trivy_gate.py run --build-number "$BUILD_NUMBER" \
              --images "$BUILD_IMAGES" --registry "$REGISTRY" --tag "$IMAGE_TAG"
          '''
        }
      }
      post {
        always {
          archiveArtifacts artifacts: "trivy-reports/${env.BUILD_NUMBER}/*.json",
            allowEmptyArchive: true, fingerprint: true
          archiveArtifacts artifacts: "trivy-reports/${env.BUILD_NUMBER}/summary.txt",
            allowEmptyArchive: true
          script {
            def summaryPath = "trivy-reports/${env.BUILD_NUMBER}/summary.txt"
            if (fileExists(summaryPath)) {
              currentBuild.description = readFile(summaryPath).trim()
            }
          }
        }
      }
    }
    stage('Registry Push: scanned images only') {
      when { expression { env.PUBLISH_IMAGES == 'true' && env.BUILD_IMAGES?.trim() } }
      steps {
        sh '''
          set -eu
          if [ "$CHECKOUT_BRANCH" != "$PUBLISH_BRANCH" ] || [ -n "${CHANGE_ID:-}" ]; then
            echo 'Image publication requires the configured non-PR branch.' >&2
            exit 1
          fi
          # Also blocks Restart from Stage from bypassing a missing/failed/stale scan.
          python3.12 scripts/trivy_gate.py verify --build-number "$BUILD_NUMBER" \
            --images "$BUILD_IMAGES" --registry "$REGISTRY" --tag "$IMAGE_TAG"
          for NAME in $BUILD_IMAGES; do
            docker push "$REGISTRY/$NAME:$IMAGE_TAG"
          done
          if [ "${PUBLISH_DEV_TAG:-false}" = 'true' ]; then
            for NAME in $BUILD_IMAGES; do
              docker tag "$REGISTRY/$NAME:$IMAGE_TAG" "$REGISTRY/$NAME:dev"
              docker push "$REGISTRY/$NAME:dev"
            done
          fi
        '''
      }
    }
    stage('ECR mirror push') {
      when { expression { params.ECR_MIRROR && env.PUBLISH_IMAGES == 'true' && env.BUILD_IMAGES?.trim() } }
      steps {
        // Gate failure is fatal, even though an ECR mirror outage remains UNSTABLE.
        sh '''
          set -eu
          python3.12 scripts/trivy_gate.py verify --build-number "$BUILD_NUMBER" \
            --images "$BUILD_IMAGES" --registry "$REGISTRY" --tag "$IMAGE_TAG"
        '''
        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
          withCredentials([
            usernamePassword(credentialsId: params.ECR_CREDENTIAL_ID.trim(),
              usernameVariable: 'AWS_ACCESS_KEY_ID', passwordVariable: 'AWS_SECRET_ACCESS_KEY'),
            string(credentialsId: params.ECR_REGISTRY_CREDENTIAL_ID.trim(), variable: 'ECR_REGISTRY')
          ]) {
            sh '''
              set -eu
              export AWS_DEFAULT_REGION=ap-northeast-2
              ECR_HOST="${ECR_REGISTRY%%/*}"
              trap 'docker logout "$ECR_HOST" >/dev/null 2>&1 || true' EXIT
              aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR_HOST"
              for NAME in $BUILD_IMAGES; do
                docker tag "$REGISTRY/$NAME:$IMAGE_TAG" "$ECR_REGISTRY/$NAME:$IMAGE_TAG"
                docker push "$ECR_REGISTRY/$NAME:$IMAGE_TAG"
              done
            '''
          }
        }
      }
    }
    stage('Development delivery and integration test') {
      when {
        expression {
          params.ENABLE_ENVIRONMENT_CD && env.PUBLISH_IMAGES == 'true' &&
            (env.BUILD_IMAGES ?: '').tokenize().contains('chargeops-csms')
        }
      }
      steps {
        // Allow 15 minutes for Dev Canary convergence plus checkout, preparation
        // and the separate integration Job (up to 210 seconds).
        timeout(time: 25, unit: 'MINUTES') {
          script {
            env.DEV_CHECK_STATE = 'pending'
            githubStatus('pending')
          }
          withCredentials([
            usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(),
              usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD'),
            file(credentialsId: params.DEV_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG')
          ]) {
            script {
              if (params.DEV_DELIVERY_MODE == 'auto') {
                env.DEV_GITOPS_OVERLAY = sh(script: '.ci-venv/bin/python scripts/env_cd.py dev-overlay', returnStdout: true).trim()
                if (env.DEV_GITOPS_OVERLAY != 'chargeops-chatops/overlays/development-rolling') { error('Dev RollingUpdate handover is required') }
              }
            }
            dir('.gitops-delivery') {
              // This build pushes GitOps commits: polling this checkout would
              // trigger the CI job again from its own deployment changes.
              checkout(changelog: false, poll: false, scm: [$class: 'GitSCM', branches: [[name: '*/main']],
                extensions: [[$class: 'LocalBranch', localBranch: 'main']],
                userRemoteConfigs: [[url: 'https://github.com/raspberry-2jo/chargeops-gitops.git',
                  credentialsId: params.GITOPS_CREDENTIAL_ID.trim()]]])
            }
            sh '''
              set -eu
              command -v kubectl
              OCPP_OPTION=''
              if [ "$RUN_OCPP_FIXTURE_TEST" = 'true' ]; then OCPP_OPTION='--ocpp-fixture-test'; fi
              CANARY_OPTION=''
              if [ "$ENABLE_CANARY" = 'true' ]; then CANARY_OPTION='--canary'; fi
              .ci-venv/bin/python scripts/env_cd.py prepare --tag "$IMAGE_TAG" --source "$(git rev-parse HEAD)" --images "$BUILD_IMAGES" $OCPP_OPTION $CANARY_OPTION
              .ci-venv/bin/python scripts/env_cd.py verify
            '''
          }
          script {
            githubStatus('success')
            env.DEV_CHECK_STATE = 'success'
            notifyDelivery('Dev verification passed')
          }
        }
      }
    }
    stage('Create source PR for dev approval') {
      when {
        expression {
          params.ENABLE_ENVIRONMENT_CD && env.PUBLISH_IMAGES == 'true' && env.CHECKOUT_BRANCH == env.PUBLISH_BRANCH &&
            env.SOURCE_COMMIT?.trim()
        }
      }
      steps {
        withCredentials([
          string(credentialsId: params.GITHUB_PR_CREDENTIAL_ID.trim(), variable: 'GITHUB_PR_TOKEN'),
          usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(), usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD'),
          file(credentialsId: params.DEV_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG')
        ]) {
          script {
            if ((env.BUILD_IMAGES ?: '').tokenize().contains('chargeops-csms')) {
              // Existing-traffic policy needs no fixture; validate one only if
              // the optional active smoke metric has explicitly been installed.
              sh '.ci-venv/bin/python scripts/canary_cd.py check-fixture'
              sh '.ci-venv/bin/python scripts/github_cd.py open-pr'
            } else {
              sh '.ci-venv/bin/python scripts/github_cd.py open-ci-pr --source "$SOURCE_COMMIT"'
            }
          }
        }
        script {
          // The required source check must pass before merge; waiting until
          // post would deadlock a PR protected by chargeops/ci.
          githubStatus('success', 'chargeops/ci')
          notifyDelivery('Source PR ready')
        }
      }
    }
    stage('Wait for source PR approval and merge') {
      when {
        expression {
          params.ENABLE_ENVIRONMENT_CD && env.PUBLISH_IMAGES == 'true' && env.CHECKOUT_BRANCH == env.PUBLISH_BRANCH &&
            (env.BUILD_IMAGES ?: '').tokenize().contains('chargeops-csms')
        }
      }
      steps {
        // 45m approval wait plus bounded final GitHub lookup/PR closure.
        timeout(time: 48, unit: 'MINUTES') {
          withCredentials([string(credentialsId: params.GITHUB_PR_CREDENTIAL_ID.trim(), variable: 'GITHUB_PR_TOKEN')]) {
            sh '.ci-venv/bin/python scripts/github_cd.py wait-merge --timeout 2700'
          }
        }
      }
    }
    stage('Promote verified digest to ops GitOps') {
      when {
        expression {
          params.ENABLE_ENVIRONMENT_CD && env.PUBLISH_IMAGES == 'true' && env.CHECKOUT_BRANCH == env.PUBLISH_BRANCH &&
            (env.BUILD_IMAGES ?: '').tokenize().contains('chargeops-csms')
        }
      }
      steps {
        script {
          if (!params.ENABLE_CANARY) { error('PR-based Ops delivery requires ENABLE_CANARY=true') }
          if (params.ENABLE_CANARY) {
            withCredentials([file(credentialsId: params.CANARY_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG'),
              usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(), usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD')]) {
              sh '.ci-venv/bin/python scripts/canary_cd.py preflight'
            }
          }
        }
        withCredentials([
          string(credentialsId: params.GITHUB_PR_CREDENTIAL_ID.trim(), variable: 'GITHUB_PR_TOKEN'),
          usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(),
            usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD'),
          file(credentialsId: params.DEV_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG')
        ]) {
          sh '''
            set -eu
            CANARY_OPTION=''
            if [ "$ENABLE_CANARY" = 'true' ]; then CANARY_OPTION='--canary'; fi
            .ci-venv/bin/python scripts/env_cd.py promote --require-approved-pr $CANARY_OPTION
          '''
        }
        script {
          if (params.ENABLE_CANARY) {
            stage('Ops Canary and automatic recovery') {
              notifyDelivery('Ops Canary started')
              withCredentials([file(credentialsId: params.CANARY_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG'),
                usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(), usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD')]) {
                timeout(time: 30, unit: 'MINUTES') {
                  sh '.ci-venv/bin/python scripts/canary_cd.py run'
                }
              }
            }
          }
        }
      }
    }
  }
  post {
    always {
      script {
        try {
            if (env.DEV_CHECK_STATE == 'pending' && ['chargeops-chatops/overlays/development-canary', 'chargeops-chatops/overlays/development-rolling'].contains(env.DEV_GITOPS_OVERLAY) && fileExists('.ci-venv/bin/python') && fileExists('artifacts/candidate.json')) {
              notifyDelivery('Dev verification failed; recovery started')
              withCredentials([
                usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(), usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD'),
                file(credentialsId: params.DEV_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG')
              ]) {
                timeout(time: 15, unit: 'MINUTES') {
                  sh '.ci-venv/bin/python scripts/env_cd.py recover-dev'
                }
              }
              notifyDelivery('Dev recovery complete')
            }
          if (env.DEV_CHECK_STATE == 'pending' && fileExists('.ci-venv/bin/python')) {
            try { githubStatus('failure') } catch (Exception ignored) { echo 'Unable to publish failed Dev status; inspect GitHub permissions.' }
          }
          if (params.ENABLE_CANARY && env.CHECKOUT_BRANCH == env.PRODUCTION_BRANCH && fileExists('.ci-venv/bin/python') && fileExists('artifacts/production-delivery.json')) {
            withCredentials([file(credentialsId: params.CANARY_KUBECONFIG_CREDENTIAL_ID.trim(), variable: 'KUBECONFIG'),
              usernamePassword(credentialsId: params.GITOPS_CREDENTIAL_ID.trim(), usernameVariable: 'GIT_USERNAME', passwordVariable: 'GIT_PASSWORD')]) {
              timeout(time: 15, unit: 'MINUTES') {
                sh '.ci-venv/bin/python scripts/canary_cd.py recover'
              }
            }
            def phase = sh(script: '''python3.12 -c 'import json; print(json.load(open("artifacts/production-delivery.json"))["phase"])' ''', returnStdout: true).trim()
            if (phase == 'recovered') { notifyDelivery('Recovery completed') }
          }
        } catch (Exception failure) {
          currentBuild.result = 'FAILURE'
          notifyDelivery('Recovery failed')
          throw failure
        } finally {
          if (env.PUBLISH_IMAGES == 'true' && fileExists('.ci-venv/bin/python')) {
            try { githubStatus(currentBuild.currentResult == 'SUCCESS' ? 'success' : 'failure', 'chargeops/ci') }
            catch (Exception ignored) { echo 'Unable to publish pipeline status; inspect GitHub permissions.' }
          }
          def event = currentBuild.currentResult == 'SUCCESS' ? 'Pipeline succeeded' : (currentBuild.currentResult == 'ABORTED' ? 'Pipeline aborted' : 'Pipeline failed')
          notifyDelivery(event)
          archiveArtifacts artifacts: 'artifacts/candidate.json,artifacts/verification.json,artifacts/dev-recovery.json,artifacts/production-delivery.json,artifacts/source-approval.json,artifacts/pull-request.json', allowEmptyArchive: true, fingerprint: true
          sh 'rm -rf .ci-venv || true'
        }
      }
    }
    success {
      echo 'CI completed: ' + (env.BUILD_IMAGES ?: '(no image changes)') + ' : ' + env.IMAGE_TAG
      echo 'Preview=' + env.PUBLISH_BRANCH + ', production=' + env.PRODUCTION_BRANCH + '; current branch=' + env.CHECKOUT_BRANCH
    }
  }
}
