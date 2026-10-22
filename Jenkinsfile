pipeline {
  agent { label 'mgmt-agent' }
  options {
    timestamps()
    disableConcurrentBuilds()
    skipDefaultCheckout(true)
    timeout(time: 45, unit: 'MINUTES')
  }
  // Job SCM branch: */kim_chanju; Script Path: Jenkinsfile.
  triggers { pollSCM('H/5 * * * *') }
  parameters {
    booleanParam(name: 'BUILD_ALL_IMAGES', defaultValue: false,
      description: 'Build all six images. The first build automatically builds all images.')
  }
  environment {
    REGISTRY = 'registry.chargeops.lab:5000/chargeops'
    APP_ROOT = 'apps'
    PUBLISH_BRANCH = 'kim_chanju'
  }
  stages {
    stage('Checkout') {
      steps {
        script {
          def checkoutVars = checkout scm
          env.CHECKOUT_BRANCH = (env.BRANCH_NAME ?: checkoutVars.GIT_BRANCH ?: env.GIT_BRANCH ?: '')
            .replaceFirst(/^refs\/heads\//, '').replaceFirst(/^origin\//, '')
          env.PUBLISH_IMAGES = (env.CHECKOUT_BRANCH == env.PUBLISH_BRANCH && !env.CHANGE_ID).toString()
          def sha = sh(script: 'git rev-parse --short=12 HEAD', returnStdout: true).trim()
          env.IMAGE_TAG = 'build-' + env.BUILD_NUMBER + '-' + sha
          echo 'IMAGE_TAG=' + env.IMAGE_TAG
          echo 'Branch=' + env.CHECKOUT_BRANCH + ', publish images=' + env.PUBLISH_IMAGES
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
                "$APP_ROOT/" Jenkinsfile requirements-dev.txt pytest.ini scripts/ci.py tests/
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
              if (path == 'Jenkinsfile' || path == 'scripts/ci.py' || path == env.APP_ROOT + '/.dockerignore') {
                selected.addAll(allImages)
              } else if (path == 'requirements-dev.txt' || path == 'pytest.ini' ||
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
          env.APP_CHANGED = (runTests || !selected.isEmpty()).toString()
          env.BUILD_IMAGES = selected.join(' ')
          echo 'Selected images: ' + (env.BUILD_IMAGES ?: '(none)')
        }
      }
    }
    stage('Test (Python 3.12)') {
      when { expression { env.APP_CHANGED == 'true' } }
      steps {
        sh '''
          set -eu
          python3.12 -m venv .ci-venv
          .ci-venv/bin/python -m pip install --upgrade pip
          .ci-venv/bin/python -m pip install -r requirements-dev.txt
          .ci-venv/bin/python -m pytest -q tests/
          python3.12 scripts/ci.py validate
        '''
      }
    }
    stage('Registry Build and Push: changed images only') {
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
          if [ "$PUBLISH_IMAGES" = 'true' ]; then
            # Publish only after every selected build succeeds.
            for NAME in $BUILD_IMAGES; do
              docker push "$REGISTRY/$NAME:$IMAGE_TAG"
            done
            for NAME in $BUILD_IMAGES; do
              docker tag "$REGISTRY/$NAME:$IMAGE_TAG" "$REGISTRY/$NAME:dev"
              docker push "$REGISTRY/$NAME:dev"
            done
          fi
        '''
      }
    }
  }
  post {
    always {
      sh 'rm -rf .ci-venv || true'
    }
    success {
      echo 'CI completed: ' + (env.BUILD_IMAGES ?: '(no image changes)') + ' : ' + env.IMAGE_TAG
      echo 'Image publication is restricted to ' + env.PUBLISH_BRANCH + '; current branch=' + env.CHECKOUT_BRANCH
    }
  }
}
