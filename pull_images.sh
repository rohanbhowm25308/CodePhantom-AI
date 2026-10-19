#!/bin/sh
# One-time download of the sandbox images (the sandbox never pulls images by itself).
for i in python:3.12-alpine node:20-alpine gcc:13 eclipse-temurin:21-jdk-alpine golang:1.22-alpine rust:1.80-alpine php:8.3-cli-alpine ruby:3.3-alpine bash:5.2; do
  docker pull "$i"
done
