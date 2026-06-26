plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.phonectl.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.phonectl.companion"
        // minSdk 31 is security-relevant: the trust model relies on Android 12+
        // per-app loopback network-namespace isolation (foreground-service SPEC §9),
        // and takeScreenshot (API 30+) is used directly.
        minSdk = 31
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-mvp"
    }

    buildTypes {
        getByName("debug") {
            isMinifyEnabled = false
        }
        getByName("release") {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    sourceSets {
        getByName("main").java.srcDirs("src/main/kotlin")
        getByName("test").java.srcDirs("src/test/kotlin")
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.preference:preference-ktx:1.2.1")
    implementation("androidx.appcompat:appcompat:1.7.0")

    // org.json ships with the Android platform at runtime; the explicit test
    // dependency provides the real implementation on the JVM unit-test classpath
    // (otherwise android.jar's stub throws). Pure serialization is tested here.
    testImplementation("org.json:json:20240303")
    testImplementation("junit:junit:4.13.2")
}
