#!/bin/bash
random=`pwgen -s 40 -1`
build_date=`date -u +"%Y-%m-%d %H:%M:%S"`
git_commit=`git rev-parse --short HEAD`
sed -i -e "s/secret_key_update_before_deployment/${random}/g" settings.py
sed -i -e "s/git_commit/${git_commit}/g" settings.py
sed -i -e "s/build_date/${build_date}/g" settings.py
sed -i -e "s/client_id/${OOO_MANAGER_CLIENT_ID}/g" settings.py
sed -i -e "s/client_secret/${OOO_MANAGER_CLIENT_SECRET}/g" settings.py

gcloud app deploy app.yaml --version ooo-manager-1-0 --project out-of-office-manager -q

git checkout -- settings.py
rm settings.py-e
