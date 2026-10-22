pipeline {
  agent { label 'mgmt-agent' }
  options {
    timestamps()
    disableConcurrentBuilds()
    skipDefaultCheckout(true)
    timeout(time: 45, unit: 'MINUTES')
  }
  // Configure the Jenkins Job SCM branch as */kim_chanju and Script Path as Jenkinsfile.
  triggers { pollSCM('H/5 * * * *') }
  parameters {
    booleanParam(name: 'BUILD_ALL_IMAGES', defaultValue: false,
      description: 'Build all nine images. The first build automatically builds all images.')
  }
  environment {
    REGISTRY = 'registry.chargeops.lab:5000/chargeops'
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
          env.FORCE_BUILD_ALL = params.BUILD_ALL_IMAGES.toString()
          echo 'Branch=' + env.CHECKOUT_BRANCH + ', publish images=' + env.PUBLISH_IMAGES
          echo 'IMAGE_TAG=' + env.IMAGE_TAG
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
          def selection = sh(script: 'python3.12 scripts/ci.py select', returnStdout: true).trim().readLines()
          env.APP_CHANGED = selection[0]
          env.BUILD_IMAGES = selection.size() > 1 ? selection[1] : ''
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
          .ci-venv/bin/python -m pytest -q
          python3.12 scripts/ci.py validate
        '''
      }
    }
    stage('Registry Build and Push: changed images only') {
      when { expression { env.BUILD_IMAGES?.trim() } }
      steps {
        sh 'python3.12 scripts/ci.py build'
      }
    }
  }
  post {
    always {
      sh 'rm -rf .ci-venv || true'
    }
    success {
      echo 'CI completed: ' + (env.BUILD_IMAGES ?: '(no image changes)') + ' : ' + env.IMAGE_TAG
      echo 'Publication branch=' + env.PUBLISH_BRANCH + '; current branch=' + env.CHECKOUT_BRANCH
      echo 'Jenkins builds and pushes images. Argo CD handles cluster deployment through GitOps.'
    }
  }
}
