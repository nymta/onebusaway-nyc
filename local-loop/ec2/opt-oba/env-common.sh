export JAVA_HOME=/usr/lib/jvm/java-11-amazon-corretto
export PATH=$JAVA_HOME/bin:/usr/local/bin:/usr/bin:/bin
export AWS_DEFAULT_REGION=us-east-1
export BUNDLE=/data/oba-bundle
export JETTY=org.eclipse.jetty:jetty-maven-plugin:9.4.51.v20230217:run
export MAIN=/opt/oba/onebusaway-nyc
export PRED=/opt/oba/onebusaway-nyc-predictions
gp(){ aws ssm get-parameter --name "$1" --with-decryption --query 'Parameter.Value' --output text; }
