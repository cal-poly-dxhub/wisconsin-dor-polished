import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserSession,
  CognitoUserAttribute,
} from 'amazon-cognito-identity-js';

const USER_POOL_ID = process.env.NEXT_PUBLIC_USER_POOL_ID!;
const CLIENT_ID = process.env.NEXT_PUBLIC_USER_POOL_CLIENT_ID!;

if (!USER_POOL_ID) throw new Error('NEXT_PUBLIC_USER_POOL_ID is not set');
if (!CLIENT_ID) throw new Error('NEXT_PUBLIC_USER_POOL_CLIENT_ID is not set');

const poolData = {
  UserPoolId: USER_POOL_ID,
  ClientId: CLIENT_ID,
};

export const userPool = new CognitoUserPool(poolData);

export interface SignUpParams {
  email: string;
  password: string;
}

export interface SignInParams {
  email: string;
  password: string;
}

export interface ConfirmSignUpParams {
  email: string;
  code: string;
}

export const signUp = (
  params: SignUpParams
): Promise<{ userSub: string; userConfirmed: boolean }> => {
  return new Promise((resolve, reject) => {
    const attributeList = [
      new CognitoUserAttribute({
        Name: 'email',
        Value: params.email,
      }),
    ];

    userPool.signUp(
      params.email,
      params.password,
      attributeList,
      [],
      (err, result) => {
        if (err) {
          reject(err);
          return;
        }
        if (!result) {
          reject(new Error('Sign up failed'));
          return;
        }
        resolve({
          userSub: result.userSub,
          userConfirmed: result.userConfirmed,
        });
      }
    );
  });
};

export const confirmSignUp = (params: ConfirmSignUpParams): Promise<void> => {
  return new Promise((resolve, reject) => {
    const cognitoUser = new CognitoUser({
      Username: params.email,
      Pool: userPool,
    });

    cognitoUser.confirmRegistration(params.code, true, err => {
      if (err) {
        reject(err);
        return;
      }
      resolve();
    });
  });
};

export const signIn = (params: SignInParams): Promise<CognitoUserSession> => {
  return new Promise((resolve, reject) => {
    const authenticationDetails = new AuthenticationDetails({
      Username: params.email,
      Password: params.password,
    });

    const cognitoUser = new CognitoUser({
      Username: params.email,
      Pool: userPool,
    });

    cognitoUser.authenticateUser(authenticationDetails, {
      onSuccess: session => {
        resolve(session);
      },
      onFailure: err => {
        reject(err);
      },
    });
  });
};

export const signOut = (): Promise<void> => {
  return new Promise(resolve => {
    const cognitoUser = userPool.getCurrentUser();
    if (cognitoUser) {
      cognitoUser.signOut();
    }
    resolve();
  });
};

export const getCurrentSession = (): Promise<CognitoUserSession | null> => {
  return new Promise((resolve, reject) => {
    const cognitoUser = userPool.getCurrentUser();
    if (!cognitoUser) {
      resolve(null);
      return;
    }

    cognitoUser.getSession(
      (err: Error | null, session: CognitoUserSession | null) => {
        if (err) {
          reject(err);
          return;
        }
        if (session && session.isValid()) {
          resolve(session);
        } else {
          resolve(null);
        }
      }
    );
  });
};

export const getIdToken = async (): Promise<string | null> => {
  const session = await getCurrentSession();
  return session ? session.getIdToken().getJwtToken() : null;
};

export const resendConfirmationCode = (email: string): Promise<void> => {
  return new Promise((resolve, reject) => {
    const cognitoUser = new CognitoUser({
      Username: email,
      Pool: userPool,
    });

    cognitoUser.resendConfirmationCode(err => {
      if (err) {
        reject(err);
        return;
      }
      resolve();
    });
  });
};
